import re
from collections import OrderedDict
from email.utils import parsedate_to_datetime

from django.utils import timezone
from django.db.models import Q

from intel.models import (
    Signal,
    SignalLocation,
    Source,
    Location,
    LocationAlias,
    ScoringRule,
)
from intel.utils.location_resolver import normalize_text, resolve_location_from_text
from intel.services.geojson_gazetteer import ensure_geojson_gazetteer
from intel.services.legacy_crawler import MedIntelCrawler


# =========================================================
# STATUS NORMALIZATION
# =========================================================

def normalize_status(value: str) -> str:
    value = (value or "").strip().lower()

    mapping = {
        "ok": "ok",
        "matched": "matched",
        "gazetteer_only": "gazetteer_only",
        "manual": "manual",
        "pending": "pending",

        "empty_loc": "empty_loc",
        "empty location": "empty_loc",
        "not_found": "not_found",
        "not found": "not_found",
        "net_err": "net_err",
        "network error": "net_err",
        "skip_noise": "skip_noise",
        "skip_too_general": "skip_too_general",
        "skip_low_conf": "skip_low_conf",
        "low confidence": "skip_low_conf",

        # setelah .lower(), legacy uppercase masuk ke key lowercase ini
        "empty_loc": "empty_loc",
        "not_found": "not_found",
        "net_err": "net_err",
        "manual": "manual",
    }

    return mapping.get(value, value or "pending")


# =========================================================
# TEXT NORMALIZATION FOR LOCATION MATCHING
# =========================================================

def normalize_match_text(value: str) -> str:
    """
    Normalisasi untuk matching alias berbasis spasi.
    Contoh:
    'Kab. Pamekasan' -> 'kab pamekasan'
    """
    if not value:
        return ""

    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9\s\-.]", " ", value)
    value = value.replace(".", " ")
    value = re.sub(r"[\s\-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_region_like(value: str) -> str:
    """
    Normalisasi untuk matching Location.normalized_name.
    Contoh:
    'Pamekasan' -> 'pamekasan'
    'Kayong Utara' -> 'kayong_utara'
    """
    value = normalize_match_text(value)
    value = value.replace(" ", "_")
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def strip_admin_prefix(value: str) -> str:
    """
    Hilangkan prefix administratif.
    Contoh:
    - Kabupaten Pamekasan -> Pamekasan
    - Kota Tarakan -> Tarakan
    - Provinsi Jawa Timur -> Jawa Timur
    """
    if not value:
        return ""

    text = str(value).strip()
    text = re.sub(
        r"^(provinsi|prov\.|kabupaten|kab\.|kab|kota|kecamatan|kec\.|kelurahan|desa)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def build_location_search_forms(value: str) -> list[str]:
    """
    Bentuk variasi untuk pencarian ke Location dan Alias.
    """
    raw = (value or "").strip()
    short = strip_admin_prefix(raw)

    forms = OrderedDict()

    for item in [raw, short]:
        item = (item or "").strip()
        if not item:
            continue

        forms[item] = True
        forms[f"Kabupaten {item}"] = True
        forms[f"Kab. {item}"] = True
        forms[f"Kab {item}"] = True
        forms[f"Kota {item}"] = True
        forms[f"Provinsi {item}"] = True
        forms[f"Kecamatan {item}"] = True
        forms[f"Dinkes {item}"] = True
        forms[f"Dinas Kesehatan {item}"] = True
        forms[f"Pemkab {item}"] = True
        forms[f"Pemkot {item}"] = True
        forms[f"Pemprov {item}"] = True

    return list(forms.keys())


# =========================================================
# LOCATION EXTRACTION FROM TITLE / RAW / CONTENT
# =========================================================

def extract_location_candidates(title: str = "", raw_location_text: str = "", content: str = "") -> list[dict]:
    """
    Ambil kandidat lokasi dari:
    1. raw_location_text
    2. judul
    3. content

    Judul diprioritaskan karena banyak berita lokal menaruh lokasi di judul.
    """

    candidates = []
    seen = set()

    sources = [
        ("raw_location_text", raw_location_text or "", 1.00),
        ("title", title or "", 0.98),
        ("content", (content or "")[:5000], 0.75),
    ]

    patterns = [
        # di Pamekasan, dari Tarakan, ke Garut, wilayah Kayong Utara
        r"\b(?:di|ke|dari|wilayah|area|sekitar|menuju|asal)\s+([A-Z][A-Za-z'`\-]+(?:\s+[A-Z][A-Za-z'`\-]+){0,4})",

        # Kabupaten Pamekasan, Kab. Garut, Kota Tarakan, Provinsi Aceh
        r"\b((?:Kabupaten|Kab\.|Kab|Kota|Provinsi)\s+[A-Z][A-Za-z'`\-]+(?:\s+[A-Z][A-Za-z'`\-]+){0,4})",

        # Kecamatan Entikong, Desa X, Kelurahan Y
        r"\b((?:Kecamatan|Kec\.|Kelurahan|Desa)\s+[A-Z][A-Za-z'`\-]+(?:\s+[A-Z][A-Za-z'`\-]+){0,4})",
    ]

    stop_words = (
        r"\b(?:turun|naik|berkat|karena|dengan|untuk|dalam|akibat|sejak|hingga|"
        r"dan|yang|jadi|mulai|aman|dikonsumsi|terbaru|bikin|heboh|harga|program|"
        r"larang|impor|unggas|gejala|banyak|dialami|anak|pengusaha|jamin|ayam)\b"
    )

    for source_name, text, weight in sources:
        if not text:
            continue

        # raw_location_text langsung dimasukkan sebagai kandidat penuh
        if source_name == "raw_location_text":
            raw = text.strip(" ,.;:-()[]{}")
            if raw:
                key = (source_name, normalize_match_text(raw))
                if key not in seen:
                    seen.add(key)
                    candidates.append({
                        "text": raw,
                        "source": source_name,
                        "weight": weight,
                    })

        for pattern in patterns:
            for match in re.findall(pattern, text):
                raw = str(match).strip(" ,.;:-()[]{}")
                if not raw:
                    continue

                # Potong kandidat yang kebablasan.
                raw = re.split(stop_words, raw, flags=re.IGNORECASE)[0].strip(" ,.;:-")

                if len(raw) < 3:
                    continue

                key = (source_name, normalize_match_text(raw))
                if key in seen:
                    continue

                seen.add(key)
                candidates.append({
                    "text": raw,
                    "source": source_name,
                    "weight": weight,
                })

    return candidates


def score_location_candidate(location: Location, source: str, method: str) -> float:
    if source == "raw_location_text":
        confidence = 0.96
    elif source == "title":
        confidence = 0.95
    else:
        confidence = 0.82

    if method == "alias":
        confidence += 0.01

    if location.level in ["city", "regency"]:
        confidence += 0.02
    elif location.level == "province":
        confidence -= 0.03

    return round(max(0.0, min(confidence, 0.99)), 2)


def find_location_by_alias_or_gazetteer(candidate_text: str, source: str):
    """
    Cari kandidat lokasi dari LocationAlias dan Location.
    Tidak hardcode wilayah.
    Semua data berasal dari master Location dan LocationAlias hasil GeoJSON.
    """

    best = None
    forms = build_location_search_forms(candidate_text)

    for form in forms:
        form_space = normalize_match_text(form)
        form_region = normalize_region_like(form)
        short_space = normalize_match_text(strip_admin_prefix(form))
        short_region = normalize_region_like(strip_admin_prefix(form))

        # 1. Alias exact
        alias_qs = (
            LocationAlias.objects.filter(
                is_active=True,
                location__is_active=True,
                location__is_false_positive=False,
            )
            .filter(
                Q(normalized_alias=form_space)
                | Q(normalized_alias=form_region)
                | Q(normalized_alias=short_space)
                | Q(normalized_alias=short_region)
                | Q(alias__iexact=form)
                | Q(alias__iexact=candidate_text)
            )
            .select_related("location", "location__parent")
        )

        for alias in alias_qs:
            loc = alias.location
            result = {
                "location": loc,
                "matched_text": candidate_text,
                "matched_form": form,
                "source": source,
                "method": "alias",
                "confidence": score_location_candidate(loc, source, "alias"),
            }

            if best is None or result["confidence"] > best["confidence"]:
                best = result

        # 2. Location exact
        loc_qs = (
            Location.objects.filter(
                is_active=True,
                is_false_positive=False,
            )
            .filter(
                Q(normalized_name=form_region)
                | Q(normalized_name=short_region)
                | Q(display_name__iexact=form)
                | Q(name__iexact=form)
                | Q(display_name__iexact=candidate_text)
                | Q(name__iexact=candidate_text)
            )
            .select_related("parent")
        )

        for loc in loc_qs:
            result = {
                "location": loc,
                "matched_text": candidate_text,
                "matched_form": form,
                "source": source,
                "method": "gazetteer",
                "confidence": score_location_candidate(loc, source, "gazetteer"),
            }

            if best is None or result["confidence"] > best["confidence"]:
                best = result

    return best


def resolve_location_from_title_raw_content(title: str = "", raw_location_text: str = "", content: str = ""):
    candidates = extract_location_candidates(
        title=title,
        raw_location_text=raw_location_text,
        content=content,
    )

    best = None

    for candidate in candidates:
        result = find_location_by_alias_or_gazetteer(
            candidate_text=candidate["text"],
            source=candidate["source"],
        )

        if not result:
            continue

        if best is None or result["confidence"] > best["confidence"]:
            best = result

    return best


def get_location_admin_detail(location: Location):
    admin_province = ""
    admin_kabkota = ""
    location_level = ""

    if not location:
        return admin_province, admin_kabkota, location_level

    location_level = location.level or ""

    if location.level == "province":
        admin_province = location.display_name or location.name or ""

    elif location.level in ["city", "regency"]:
        admin_kabkota = location.display_name or location.name or ""

        if location.parent:
            admin_province = location.parent.display_name or location.parent.name or ""
        elif location.province_code:
            province = Location.objects.filter(
                level="province",
                province_code=location.province_code,
                is_active=True,
                is_false_positive=False,
            ).first()
            if province:
                admin_province = province.display_name or province.name or ""

    return admin_province, admin_kabkota, location_level


def apply_location_to_signal(signal: Signal, location_obj, method: str, confidence: float, matched_text: str):
    if not location_obj:
        return False

    admin_province, admin_kabkota, location_level = get_location_admin_detail(location_obj)

    if not signal.raw_location_text:
        signal.raw_location_text = matched_text or ""

    signal.admin_province = admin_province
    signal.admin_kabkota = admin_kabkota
    signal.location_level = location_level
    signal.geocode_status = "gazetteer_only"
    signal.approved_for_mapping = True

    signal.save(update_fields=[
        "raw_location_text",
        "admin_province",
        "admin_kabkota",
        "location_level",
        "geocode_status",
        "approved_for_mapping",
        "updated_at",
    ])

    SignalLocation.objects.update_or_create(
        signal=signal,
        is_primary=True,
        defaults={
            "location": location_obj,
            "raw_location_text": matched_text or signal.raw_location_text,
            "confidence": confidence or 0.80,
            "method": method or "auto",
        },
    )

    return True


# =========================================================
# LEGACY FALLBACK FROM CRAWLER ADMIN FIELDS
# =========================================================

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


# =========================================================
# DATETIME / SOURCE / SCORING
# =========================================================

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


# =========================================================
# INGEST
# =========================================================

def ingest_legacy_row(row: dict):
    source_name = row.get("sumber", "Google News")
    source_url = row.get("final_url") or row.get("link") or ""

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

        scoring_reason = scoring_reason or fallback_reason
        scoring_breakdown = scoring_breakdown or fallback_breakdown
        risk_level = risk_level or fallback_risk

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

    location_obj = None
    method = ""
    confidence = 0.0
    matched_text = raw_location_text

    # 1. Resolver lama berbasis raw_location_text
    if raw_location_text:
        result = resolve_location_from_text(raw_location_text)
        location_obj = result.location_obj
        method = result.method
        confidence = result.confidence
        matched_text = raw_location_text

    # 2. Fallback dari admin_province/admin_kabkota crawler lama
    if not location_obj:
        fallback_loc, fallback_method, fallback_conf = fallback_location_from_legacy_fields(row)
        if fallback_loc:
            location_obj = fallback_loc
            method = fallback_method
            confidence = fallback_conf
            matched_text = raw_location_text or admin_kabkota or admin_province

    # 3. Resolver baru berbasis title + raw + content.
    # Ini yang membuat judul seperti "di Pamekasan", "di Tarakan", "di Cilegon"
    # bisa langsung match ke gazetteer nasional.
    if not location_obj:
        title_resolved = resolve_location_from_title_raw_content(
            title=title,
            raw_location_text=raw_location_text,
            content=content,
        )

        if title_resolved:
            location_obj = title_resolved["location"]
            method = title_resolved["method"]
            confidence = title_resolved["confidence"]
            matched_text = title_resolved["matched_text"]

    if location_obj:
        apply_location_to_signal(
            signal=signal,
            location_obj=location_obj,
            method=method or "auto",
            confidence=confidence or 0.80,
            matched_text=matched_text or raw_location_text,
        )
    else:
        if signal.geocode_status in ["", "pending"]:
            signal.geocode_status = "skip_low_conf"
            signal.save(update_fields=["geocode_status", "updated_at"])

    return signal, created, location_obj


def run_legacy_crawler_ingest():
    gazetteer_result = ensure_geojson_gazetteer()
    print("=== GEOJSON GAZETTEER ===")
    print(gazetteer_result)

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