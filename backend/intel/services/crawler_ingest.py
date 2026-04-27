from intel.models import Signal, SignalLocation, Source
from intel.utils.location_resolver import resolve_location_from_text
from intel.services.signal_dedup import should_skip_as_noise

def ingest_article(item: dict):
    source_obj, _ = Source.objects.get_or_create(
        name=item.get("source_name", "Unknown"),
        defaults={
            "base_url": item.get("base_url", ""),
            "rss_url": item.get("rss_url", ""),
            "country_code": "ID",
            "is_active": True,
        }
    )

    title = item.get("title", "") or ""
    source_url = item.get("source_url", "") or ""
    disease_tag = item.get("disease_tag", "") or ""
    raw_location_text = item.get("raw_location_text", "") or ""

    skip_noise, existing_signal = should_skip_as_noise(
        source_url=source_url,
        resolved_url=item.get("resolved_url", "") or "",
        title=title,
        disease_tag=disease_tag,
        raw_location_text=raw_location_text,
        source_name=source_obj.name if source_obj else "",
    )

    if skip_noise:
        return existing_signal, False

    signal, created = Signal.objects.update_or_create(
        source_url=item["source_url"],
        defaults={
            "title": title,
            "content": item.get("content", ""),
            "source": source_obj,
            "published_at": item.get("published_at"),
            "disease_tag": disease_tag,
            "threat_score": item.get("threat_score", 0),
            "raw_location_text": raw_location_text,
            "status": existing_signal.status if existing_signal else item.get("status", "raw"),
        }
    )

    raw_loc = signal.raw_location_text or ""
    result = resolve_location_from_text(raw_loc)

    if result.location_obj:
        SignalLocation.objects.update_or_create(
            signal=signal,
            is_primary=True,
            defaults={
                "location": result.location_obj,
                "raw_location_text": raw_loc,
                "confidence": result.confidence,
                "method": result.method,
            }
        )

    return signal, created