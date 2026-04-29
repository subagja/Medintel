import math
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from intel.models import (
    Source,
    Location,
    Signal,
    SignalLocation,
    ResolvedSourceURL,
)
from intel.services.signal_dedup import should_skip_as_noise


def clean_str(val):
    if val is None:
        return ""

    if isinstance(val, float) and math.isnan(val):
        return ""

    return str(val).strip()


def clean_int(val, default=0):
    try:
        if pd.isna(val):
            return default
        return int(float(val))
    except Exception:
        return default


def clean_float(val):
    try:
        if pd.isna(val):
            return None
        return float(val)
    except Exception:
        return None


def parse_dt(val):
    s = clean_str(val)

    if not s:
        return None

    try:
        return pd.to_datetime(s, utc=True).to_pydatetime()
    except Exception:
        pass

    try:
        return parse_datetime(s)
    except Exception:
        return None


def normalize_region_code(value):
    value = clean_str(value)

    if not value:
        return ""

    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = value.replace("/", " ")
    value = re.sub(r"[^a-z0-9\s_-]", "", value)
    value = re.sub(r"[\s\-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")

    return value


def is_google_news_url(url: str) -> bool:
    url = clean_str(url).lower()
    return "news.google.com" in url or "google.com/rss" in url


def normalize_url(value: str) -> str:
    value = clean_str(value)

    if not value:
        return ""

    return value


def pick_source_and_resolved_url(row):
    """
    Konvensi baru:
    - source_url   = URL awal dari crawler/RSS.
    - resolved_url = URL artikel asli/final URL bila tersedia.

    CSV lama kadang hanya punya final_url/link.
    Maka kita coba baca beberapa nama kolom.
    """
    raw_link = (
        clean_str(row.get("link"))
        or clean_str(row.get("source_url"))
        or clean_str(row.get("google_url"))
        or clean_str(row.get("rss_url"))
        or clean_str(row.get("url"))
    )

    final_url = (
        clean_str(row.get("final_url"))
        or clean_str(row.get("resolved_url"))
        or clean_str(row.get("article_url"))
    )

    source_url = normalize_url(raw_link or final_url)
    resolved_url = normalize_url(final_url)

    # Kalau final_url masih Google News, jangan dianggap resolved.
    if resolved_url and is_google_news_url(resolved_url):
        resolved_url = ""

    # Kalau source_url kosong tapi resolved_url ada, jadikan resolved_url sebagai source_url juga.
    if not source_url and resolved_url:
        source_url = resolved_url

    # Kalau source_url dan resolved_url sama-sama artikel asli, tetap simpan resolved_url.
    if resolved_url and resolved_url == source_url and not is_google_news_url(source_url):
        resolution_status = "resolved"
        resolution_method = "import_final_url"
    elif resolved_url:
        resolution_status = "resolved"
        resolution_method = "import_final_url"
    else:
        resolution_status = "unresolved"
        resolution_method = ""

    return source_url, resolved_url, resolution_status, resolution_method


def normalize_domain_from_url(url: str) -> str:
    url = clean_str(url)

    if not url:
        return ""

    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
    except Exception:
        return ""

    if host.startswith("www."):
        host = host[4:]

    return host


class Command(BaseCommand):
    help = "Import old crawling CSV into Source, Signal, Location, and SignalLocation"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            required=True,
            help="Path to CSV file, e.g. ../data/raw/data_intel_raw.csv",
        )
        parser.add_argument(
            "--respect-noise",
            action="store_true",
            help="Jika aktif, row yang match dengan existing noise akan diskip.",
        )

    def handle(self, *args, **options):
        file_path = Path(options["file"])

        if not file_path.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        df = pd.read_csv(file_path)

        created_sources = 0
        created_locations = 0
        created_signals = 0
        created_links = 0
        skipped = 0
        skipped_noise = 0
        updated_signals = 0
        updated_links = 0
        cached_resolved_urls = 0

        for _, row in df.iterrows():
            title = clean_str(row.get("judul")) or clean_str(row.get("title"))
            source_name = clean_str(row.get("sumber")) or clean_str(row.get("source"))
            source_url, resolved_url, url_resolution_status, url_resolution_method = pick_source_and_resolved_url(row)

            summary = clean_str(row.get("summary")) or clean_str(row.get("content"))
            disease_tag = clean_str(row.get("penyakit_tag")) or clean_str(row.get("disease_tag"))
            raw_location_text = clean_str(row.get("lokasi_mentah")) or clean_str(row.get("raw_location_text"))
            geocode_status = clean_str(row.get("geocode_status")) or "pending"
            threat_score = clean_int(row.get("skor_ancaman") or row.get("threat_score"), default=0)

            published_at = parse_dt(row.get("tanggal") or row.get("published_at"))
            lat = clean_float(row.get("lat"))
            lon = clean_float(row.get("lon"))

            admin_province = clean_str(row.get("admin_province"))
            admin_kabkota = clean_str(row.get("admin_kabkota"))
            level_lokasi = clean_str(row.get("level_lokasi") or row.get("location_level")).lower()

            if not title or not source_url:
                skipped += 1
                continue

            skip_noise, existing_signal = should_skip_as_noise(
                source_url=source_url,
                resolved_url=resolved_url or source_url,
                title=title,
                disease_tag=disease_tag,
                raw_location_text=raw_location_text,
                source_name=source_name,
                respect_noise=respect_noise,
            )

            if skip_noise:
                skipped_noise += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"SKIP noise: existing_id={existing_signal.id} title={existing_signal.title[:80]}"
                    )
                )
                continue

            source_obj = None

            if source_name:
                base_url = ""
                if resolved_url:
                    domain = normalize_domain_from_url(resolved_url)
                    if domain:
                        base_url = f"https://{domain}"

                source_obj, source_created = Source.objects.get_or_create(
                    name=source_name,
                    defaults={
                        "base_url": base_url,
                        "rss_url": "",
                        "country_code": "ID",
                        "is_active": True,
                    },
                )

                if source_created:
                    created_sources += 1

                if not source_obj.base_url and base_url:
                    source_obj.base_url = base_url
                    source_obj.save(update_fields=["base_url", "updated_at"])

            signal_obj, signal_created = Signal.objects.get_or_create(
                source_url=source_url,
                defaults={
                    "title": title[:500],
                    "content": summary,
                    "source": source_obj,
                    "published_at": published_at,
                    "crawled_at": published_at,
                    "disease_tag": disease_tag,
                    "threat_score": threat_score,
                    "raw_location_text": raw_location_text,
                    "geocode_status": geocode_status.lower() if geocode_status else "pending",
                    "status": "raw",
                    "approved_for_mapping": False,
                    "resolved_url": resolved_url,
                    "url_resolution_status": url_resolution_status,
                    "url_resolution_method": url_resolution_method,
                    "url_resolution_error": "",
                },
            )

            if signal_created:
                created_signals += 1
            else:
                if signal_obj.status == "noise" and respect_noise:
                    skipped_noise += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"SKIP existing noise: existing_id={signal_obj.id} title={signal_obj.title[:80]}"
                        )
                    )
                    continue

                changed = False
                update_fields = []

                def set_if_empty(field_name, value):
                    nonlocal changed

                    if value in [None, ""]:
                        return

                    if not getattr(signal_obj, field_name):
                        setattr(signal_obj, field_name, value)
                        update_fields.append(field_name)
                        changed = True

                set_if_empty("title", title[:500])
                set_if_empty("content", summary)
                set_if_empty("source", source_obj)
                set_if_empty("published_at", published_at)
                set_if_empty("crawled_at", published_at)
                set_if_empty("disease_tag", disease_tag)
                set_if_empty("raw_location_text", raw_location_text)

                if signal_obj.threat_score == 0 and threat_score:
                    signal_obj.threat_score = threat_score
                    update_fields.append("threat_score")
                    changed = True

                if signal_obj.geocode_status in ["", "PENDING", "pending"] and geocode_status:
                    signal_obj.geocode_status = geocode_status.lower()
                    update_fields.append("geocode_status")
                    changed = True

                if resolved_url and not signal_obj.resolved_url:
                    signal_obj.resolved_url = resolved_url
                    signal_obj.url_resolution_status = "resolved"
                    signal_obj.url_resolution_method = "import_final_url"
                    signal_obj.url_resolution_error = ""
                    update_fields.extend([
                        "resolved_url",
                        "url_resolution_status",
                        "url_resolution_method",
                        "url_resolution_error",
                    ])
                    changed = True

                if changed:
                    update_fields.append("updated_at")
                    signal_obj.save(update_fields=list(set(update_fields)))
                    updated_signals += 1

            if resolved_url:
                ResolvedSourceURL.objects.update_or_create(
                    original_url=source_url,
                    defaults={
                        "resolved_url": resolved_url,
                        "source_name": source_name,
                        "title": title,
                        "method": "import_final_url",
                        "confidence": 0.9,
                        "is_manual": False,
                    },
                )
                cached_resolved_urls += 1

            province_obj = None

            if admin_province:
                province_obj, province_created = Location.objects.get_or_create(
                    normalized_name=normalize_region_code(admin_province),
                    level="province",
                    defaults={
                        "name": admin_province,
                        "display_name": admin_province,
                        "country_code": "ID",
                        "province_code": normalize_region_code(admin_province),
                        "lat": lat if level_lokasi == "province" else None,
                        "lon": lon if level_lokasi == "province" else None,
                        "is_active": True,
                    },
                )

                if province_created:
                    created_locations += 1

            location_obj = None

            if admin_kabkota:
                loc_level = "city"
                kab_lower = admin_kabkota.lower()

                if "kab" in kab_lower or "kabupaten" in kab_lower:
                    loc_level = "regency"

                location_obj, location_created = Location.objects.get_or_create(
                    normalized_name=normalize_region_code(admin_kabkota),
                    level=loc_level,
                    parent=province_obj,
                    defaults={
                        "name": admin_kabkota,
                        "display_name": admin_kabkota,
                        "country_code": "ID",
                        "province_code": province_obj.province_code if province_obj else "",
                        "city_regency_code": normalize_region_code(admin_kabkota),
                        "lat": lat if level_lokasi in ["city", "regency"] else None,
                        "lon": lon if level_lokasi in ["city", "regency"] else None,
                        "is_active": True,
                    },
                )

                if location_created:
                    created_locations += 1

            elif province_obj:
                location_obj = province_obj

            if location_obj or raw_location_text:
                link_obj, link_created = SignalLocation.objects.get_or_create(
                    signal=signal_obj,
                    is_primary=True,
                    defaults={
                        "location": location_obj,
                        "raw_location_text": raw_location_text,
                        "confidence": None,
                        "method": "auto",
                    },
                )

                if link_created:
                    created_links += 1
                else:
                    changed = False
                    update_fields = []

                    if not link_obj.location and location_obj:
                        link_obj.location = location_obj
                        update_fields.append("location")
                        changed = True

                    if not link_obj.raw_location_text and raw_location_text:
                        link_obj.raw_location_text = raw_location_text
                        update_fields.append("raw_location_text")
                        changed = True

                    if changed:
                        update_fields.append("updated_at")
                        link_obj.save(update_fields=update_fields)
                        updated_links += 1

        self.stdout.write(self.style.SUCCESS("Import finished"))
        self.stdout.write(f"Created sources: {created_sources}")
        self.stdout.write(f"Created locations: {created_locations}")
        self.stdout.write(f"Created signals: {created_signals}")
        self.stdout.write(f"Updated signals: {updated_signals}")
        self.stdout.write(f"Created signal links: {created_links}")
        self.stdout.write(f"Updated signal links: {updated_links}")
        self.stdout.write(f"Cached resolved URLs: {cached_resolved_urls}")
        self.stdout.write(f"Skipped rows: {skipped}")
        self.stdout.write(f"Noise filtering    : analyst validation mode")