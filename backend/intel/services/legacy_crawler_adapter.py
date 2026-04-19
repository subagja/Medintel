from datetime import datetime
from email.utils import parsedate_to_datetime

from django.utils import timezone

from intel.models import Signal, SignalLocation, Source, Location
from intel.utils.location_resolver import resolve_location_from_text

# sesuaikan import path-nya dengan lokasi file crawler lama kamu
from intel.services.legacy_crawler import MedIntelCrawler


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
    # sesuaikan kalau kamu punya enum status spesifik
    return "raw"


def ingest_legacy_row(row: dict):
    source_name = row.get("sumber", "Google News")
    source_url = row.get("final_url") or row.get("link") or ""
    title = row.get("judul", "") or ""
    content = row.get("summary", "") or ""
    raw_location_text = row.get("lokasi_mentah", "") or ""
    disease_tag = row.get("penyakit_tag", "") or ""

    try:
        threat_score = int(float(row.get("skor_ancaman", 0) or 0))
    except Exception:
        threat_score = 0

    published_at = parse_datetime_safe(row.get("tanggal"))
    source_obj = get_or_create_source(source_name=source_name)

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
            "raw_location_text": raw_location_text,
            "geocode_status": (row.get("geocode_status", "") or "").lower(),
            "status": map_status_from_row(row),
            "is_high_risk": threat_score >= 70,
            "approved_for_mapping": True if raw_location_text else False,
        },
    )

    # resolve pakai raw_location_text dari crawler lama
    result = resolve_location_from_text(raw_location_text)

    if result.location_obj:
        SignalLocation.objects.update_or_create(
            signal=signal,
            is_primary=True,
            defaults={
                "location": result.location_obj,
                "raw_location_text": raw_location_text,
                "confidence": result.confidence,
                "method": f"legacy_crawler:{result.method}",
            },
        )

    return signal, created, result


def run_legacy_crawler_ingest():
    crawler = MedIntelCrawler()
    rows = crawler.run()

    created_count = 0
    updated_count = 0
    matched_count = 0

    for row in rows:
        signal, created, result = ingest_legacy_row(row)

        if created:
            created_count += 1
        else:
            updated_count += 1

        if result.location_obj:
            matched_count += 1

    return {
        "total_rows": len(rows),
        "created": created_count,
        "updated": updated_count,
        "matched_locations": matched_count,
    }