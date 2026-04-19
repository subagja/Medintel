import math
import re
import unicodedata
import pandas as pd
from pathlib import Path
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime
from intel.models import Source, Location, Signal, SignalLocation


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

    # Contoh format: Mon, 13 Apr 2026 07:42:44 GMT
    try:
        return pd.to_datetime(s, utc=True).to_pydatetime()
    except Exception:
        pass

    try:
        return parse_datetime(s)
    except Exception:
        return None


class Command(BaseCommand):
    help = "Import old crawling CSV into Source, Signal, Location, and SignalLocation"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            required=True,
            help="Path to CSV file, e.g. ../data/raw/data_intel_raw.csv",
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

        for _, row in df.iterrows():
            title = clean_str(row.get("judul"))
            source_name = clean_str(row.get("sumber"))
            source_url = clean_str(row.get("final_url")) or clean_str(row.get("link"))
            summary = clean_str(row.get("summary"))
            disease_tag = clean_str(row.get("penyakit_tag"))
            raw_location_text = clean_str(row.get("lokasi_mentah"))
            geocode_status = clean_str(row.get("geocode_status")) or "PENDING"
            threat_score = clean_int(row.get("skor_ancaman"), default=0)

            published_at = parse_dt(row.get("tanggal"))
            lat = clean_float(row.get("lat"))
            lon = clean_float(row.get("lon"))

            admin_province = clean_str(row.get("admin_province"))
            admin_kabkota = clean_str(row.get("admin_kabkota"))
            level_lokasi = clean_str(row.get("level_lokasi")).lower()

            if not title or not source_url:
                skipped += 1
                continue

            source_obj = None
            if source_name:
                source_obj, source_created = Source.objects.get_or_create(
                    name=source_name,
                    defaults={
                        "base_url": "",
                        "rss_url": "",
                        "country_code": "ID",
                        "is_active": True,
                    },
                )
                if source_created:
                    created_sources += 1

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
                    "geocode_status": geocode_status if geocode_status else "PENDING",
                    "status": "raw",
                    "approved_for_mapping": False,
                },
            )

            if signal_created:
                created_signals += 1
            else:
                # update ringan bila record sudah ada
                changed = False

                if not signal_obj.title and title:
                    signal_obj.title = title[:500]
                    changed = True
                if not signal_obj.content and summary:
                    signal_obj.content = summary
                    changed = True
                if not signal_obj.source and source_obj:
                    signal_obj.source = source_obj
                    changed = True
                if not signal_obj.published_at and published_at:
                    signal_obj.published_at = published_at
                    changed = True
                if not signal_obj.disease_tag and disease_tag:
                    signal_obj.disease_tag = disease_tag
                    changed = True
                if signal_obj.threat_score == 0 and threat_score:
                    signal_obj.threat_score = threat_score
                    changed = True
                if not signal_obj.raw_location_text and raw_location_text:
                    signal_obj.raw_location_text = raw_location_text
                    changed = True
                if signal_obj.geocode_status == "PENDING" and geocode_status:
                    signal_obj.geocode_status = geocode_status
                    changed = True

                if changed:
                    signal_obj.save()

            province_obj = None
            if admin_province:
                province_obj, province_created = Location.objects.get_or_create(
                    normalized_name=admin_province.strip().lower(),
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
                    normalized_name=admin_kabkota.strip().lower(),
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
                    if not link_obj.location and location_obj:
                        link_obj.location = location_obj
                        changed = True
                    if not link_obj.raw_location_text and raw_location_text:
                        link_obj.raw_location_text = raw_location_text
                        changed = True
                    if changed:
                        link_obj.save()

        self.stdout.write(self.style.SUCCESS("Import finished"))
        self.stdout.write(f"Created sources: {created_sources}")
        self.stdout.write(f"Created locations: {created_locations}")
        self.stdout.write(f"Created signals: {created_signals}")
        self.stdout.write(f"Created signal links: {created_links}")
        self.stdout.write(f"Skipped rows: {skipped}")
        
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