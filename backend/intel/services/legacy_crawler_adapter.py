from email.utils import parsedate_to_datetime

from django.utils import timezone
from django.db.models import Q

from intel.models import Signal, SignalLocation, Source, Location, ScoringRule
from intel.utils.location_resolver import normalize_text, resolve_location_from_text

from intel.services.legacy_crawler import MedIntelCrawler


def normalize_status(value: str) -> str:
    value = (value or "").strip().lower()

    mapping = {
        "ok": "ok",
        "matched": "matched",
        "gazetteer_only": "gazetteer_only",
        "manual": "manual",
        "pending": "pending",

        "empty_loc": "empty_loc",
        "not_found": "not_found",
        "net_err": "net_err",
        "skip_noise": "skip_noise",
        "skip_too_general": "skip_too_general",
        "skip_low_conf": "skip_low_conf",

        "empty location": "empty_loc",
        "not found": "not_found",
        "network error": "net_err",
        "low confidence": "skip_low_conf",

        # legacy uppercase from crawler
        "OK": "ok",
        "EMPTY_LOC": "empty_loc",
        "NOT_FOUND": "not_found",
        "NET_ERR": "net_err",
        "SKIP_NOISE": "skip_noise",
        "SKIP_TOO_GENERAL": "skip_too_general",
        "SKIP_LOW_CONF": "skip_low_conf",
        "MANUAL": "manual",
        "PENDING": "pending",
    }

    return mapping.get(value, value or "pending")


def fallback_location_from_legacy_fields(row: dict):
    admin_province = (row.get("admin_province") or "").strip()
    admin_kabkota = (row.get("admin_kabkota") or "").strip()
    level_lokasi = (row.get("level_lokasi") or "").strip().lower()

    province_norm = normalize_text(admin_province)
    kabkota_norm = normalize_text(admin_kabkota)

    province_obj = None

    if province_norm:
        province_obj = Location.objects.filter(
            level="province",
            is_active=True,
            is_false_positive=False,
        ).filter(
            Q(normalized_name=province_norm)
            | Q(normalized_name=province_norm.replace("_", " "))
            | Q(normalized_name=province_norm.replace(" ", "_"))
            | Q(display_name__iexact=admin_province)
            | Q(name__iexact=admin_province)
            | Q(province_code__iexact=province_norm)
        ).first()

    if kabkota_norm:
        qs = Location.objects.filter(
            is_active=True,
            is_false_positive=False,
        ).filter(
            Q(normalized_name=kabkota_norm)
            | Q(normalized_name=kabkota_norm.replace("_", " "))
            | Q(normalized_name=kabkota_norm.replace(" ", "_"))
            | Q(display_name__iexact=admin_kabkota)
            | Q(name__iexact=admin_kabkota)
        )

        if level_lokasi in ["city", "kota"]:
            qs = qs.filter(level="city")
        elif level_lokasi in ["regency", "kabupaten"]:
            qs = qs.filter(level="regency")
        else:
            qs = qs.filter(level__in=["city", "regency"])

        if province_obj:
            qs = qs.filter(parent=province_obj)

        loc = qs.first()
        if loc:
            return loc, "legacy_admin_normalized", 0.90

    if province_obj:
        return province_obj, "legacy_admin_province_normalized", 0.82

    return None, "", 0.0


def parse_datetime_safe(value):
    if not value:
        return None

    try:
        dt = parsedate_to_datetime(value)
        if dt is None:
            return None

        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())

        return dt
    except Exception:
        return None


def get_or_create_source(source_name: str, base_url: str = "", rss_url: str = ""):
    source_name = (source_name or "Google News").strip()

    obj, _ = Source.objects.get_or_create(
        name=source_name,
        defaults={
            "base_url": base_url or "",
            "rss_url": rss_url or "",
            "country_code": "ID",
            "is_active": True,
        },
    )
    return obj


def map_status_from_row(row: dict) -> str:
    return "raw"


def classify_risk(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def build_scoring_reason_from_rules(
    title: str,
    content: str,
    disease_tag: str,
    threat_score: int,
    row: dict,
) -> tuple[str, dict, str]:
    """
    Fallback scoring reasoning.

    Dipakai jika legacy_crawler.py belum mengirim:
    - scoring_reason
    - scoring_breakdown
    - risk_level

    Reasoning dibuat dari:
    - base score
    - disease tag
    - event types NLP
    - severity NLP
    - active ScoringRule
    - angka kasus/korban dalam teks
    """

    text = " ".join([
        title or "",
        content or "",
        disease_tag or "",
        row.get("detected_diseases", "") or "",
        row.get("event_types", "") or "",
    ]).lower()

    reasons = []
    matched_rules = []

    reasons.append("Base score mengikuti hasil perhitungan crawler legacy.")

    if disease_tag:
        reasons.append(f"Kategori penyakit hasil crawling: {disease_tag}.")

    detected_diseases = row.get("detected_diseases", "") or ""
    if detected_diseases:
        reasons.append(f"Penyakit terdeteksi oleh NLP: {detected_diseases}.")

    event_types = row.get("event_types", "") or ""
    if event_types:
        reasons.append(f"Indikator kejadian terdeteksi oleh NLP: {event_types}.")

    severity_nlp = row.get("severity_nlp", 0) or 0
    try:
        severity_nlp = int(float(severity_nlp))
    except Exception:
        severity_nlp = 0

    if severity_nlp:
        reasons.append(f"Severity NLP terdeteksi sebesar {severity_nlp}.")

    for rule in ScoringRule.objects.filter(is_active=True).order_by("-weight"):
        keyword = (rule.keyword or rule.name or "").strip().lower()
        if not keyword:
            continue

        if keyword in text:
            matched_rules.append({
                "rule_id": rule.id,
                "name": rule.name,
                "keyword": keyword,
                "weight": rule.weight,
                "rule_type": rule.rule_type,
            })
            reasons.append(
                f"Rule cocok: {rule.name} / keyword '{keyword}' dengan bobot +{rule.weight}."
            )

    import re
    numbers = re.findall(r"\b\d+\b", text)
    if numbers:
        reasons.append("Terdapat angka dalam teks yang dapat mengindikasikan jumlah kasus/korban.")

    risk_level = classify_risk(threat_score)

    if risk_level == "high":
        reasons.append("Klasifikasi akhir: high risk karena skor >= 70.")
    elif risk_level == "medium":
        reasons.append("Klasifikasi akhir: medium risk karena skor 40-69.")
    else:
        reasons.append("Klasifikasi akhir: low risk karena skor < 40.")

    breakdown = {
        "source": "legacy_crawler_adapter_fallback",
        "final_score": threat_score,
        "risk_level": risk_level,
        "disease_tag": disease_tag,
        "detected_diseases": detected_diseases,
        "event_types": event_types,
        "severity_nlp": severity_nlp,
        "matched_rules": matched_rules,
        "numbers_detected": numbers[:20],
    }

    reason_text = "\n".join([f"- {item}" for item in reasons])

    return reason_text, breakdown, risk_level


def ingest_legacy_row(row: dict):
    source_name = row.get("sumber", "Google News")
    source_url = row.get("final_url") or row.get("link") or ""

    # Hindari source_url kosong karena field source_url unique=True
    if not source_url:
        source_url = f"legacy://{hash(str(row))}"

    title = row.get("judul", "") or ""
    content = row.get("body") or row.get("combined_text") or row.get("summary", "") or ""

    raw_location_text = row.get("lokasi_mentah", "") or ""
    admin_province = row.get("admin_province", "") or ""
    admin_kabkota = row.get("admin_kabkota", "") or ""
    location_level = row.get("level_lokasi", "") or ""

    if not raw_location_text:
        if admin_kabkota:
            raw_location_text = admin_kabkota
        elif admin_province:
            raw_location_text = admin_province

    disease_tag = row.get("penyakit_tag", "") or ""

    try:
        threat_score = int(float(row.get("skor_ancaman", 0) or 0))
    except Exception:
        threat_score = 0

    scoring_reason = row.get("scoring_reason", "") or ""
    scoring_breakdown = row.get("scoring_breakdown") or {}
    risk_level = row.get("risk_level", "") or ""

    if not scoring_reason or not scoring_breakdown or not risk_level:
        fallback_reason, fallback_breakdown, fallback_risk = build_scoring_reason_from_rules(
            title=title,
            content=content,
            disease_tag=disease_tag,
            threat_score=threat_score,
            row=row,
        )

        if not scoring_reason:
            scoring_reason = fallback_reason

        if not scoring_breakdown:
            scoring_breakdown = fallback_breakdown

        if not risk_level:
            risk_level = fallback_risk

    published_at = parse_datetime_safe(row.get("tanggal"))
    source_obj = get_or_create_source(source_name=source_name)

    geocode_status = normalize_status(row.get("geocode_status", ""))

    signal, created = Signal.objects.update_or_create(
        source_url=source_url,
        defaults={
            "title": title,
            "content": content,
            "source": source_obj,
            "published_at": published_at,
            "crawled_at": timezone.now(),

            "disease_tag": disease_tag,
            "threat_score": threat_score,
            "risk_level": risk_level,
            "scoring_reason": scoring_reason,
            "scoring_breakdown": scoring_breakdown,

            "raw_location_text": raw_location_text,
            "admin_province": admin_province,
            "admin_kabkota": admin_kabkota,
            "location_level": location_level,
            "geocode_status": geocode_status,

            "status": map_status_from_row(row),
            "is_high_risk": threat_score >= 70,
            "approved_for_mapping": True if raw_location_text else False,
        },
    )

    result = resolve_location_from_text(raw_location_text)

    location_obj = result.location_obj
    method = result.method
    confidence = result.confidence

    if not location_obj:
        fallback_loc, fallback_method, fallback_conf = fallback_location_from_legacy_fields(row)
        if fallback_loc:
            location_obj = fallback_loc
            method = fallback_method
            confidence = fallback_conf

    if location_obj:
        SignalLocation.objects.update_or_create(
            signal=signal,
            is_primary=True,
            defaults={
                "location": location_obj,
                "raw_location_text": raw_location_text,
                "confidence": confidence,
                "method": method or "auto",
            },
        )

        # Kalau awalnya empty/lowconf tapi berhasil match via resolver/fallback,
        # naikkan status menjadi gazetteer_only.
        if signal.geocode_status in ["empty_loc", "not_found", "skip_low_conf", "pending", ""]:
            signal.geocode_status = "gazetteer_only"
            signal.approved_for_mapping = True
            signal.save(update_fields=["geocode_status", "approved_for_mapping", "updated_at"])

    return signal, created, location_obj


def run_legacy_crawler_ingest():
    crawler = MedIntelCrawler()
    rows = crawler.run()

    created_count = 0
    updated_count = 0
    matched_count = 0

    for row in rows:
        signal, created, location_obj = ingest_legacy_row(row)

        if created:
            created_count += 1
        else:
            updated_count += 1

        if location_obj:
            matched_count += 1

    return {
        "total_rows": len(rows),
        "created": created_count,
        "updated": updated_count,
        "matched_locations": matched_count,
    }