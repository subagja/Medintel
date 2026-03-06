# import csv
# import hashlib
# from datetime import datetime
# from typing import Optional

# from django.core.management.base import BaseCommand, CommandError
# from django.utils import timezone

# from intel.models import Source, Signal, SignalLocation


# def parse_datetime_any(s: str) -> Optional[datetime]:
#     if not s:
#         return None
#     s = str(s).strip()
#     fmts = [
#         "%a, %d %b %Y %H:%M:%S %Z",  # Fri, 27 Feb 2026 04:47:50 GMT
#         "%a, %d %b %Y %H:%M:%S %z",
#         "%Y-%m-%d %H:%M:%S",
#         "%Y-%m-%d",
#     ]
#     for fmt in fmts:
#         try:
#             dt = datetime.strptime(s, fmt)
#             # if no tzinfo, treat as local
#             if dt.tzinfo is None:
#                 return dt
#             return dt.astimezone(timezone.get_current_timezone()).replace(tzinfo=None)
#         except Exception:
#             continue
#     # fallback: try take YYYY-MM-DD
#     try:
#         if len(s) >= 10 and s[4] == "-" and s[7] == "-":
#             return datetime.strptime(s[:10], "%Y-%m-%d")
#     except Exception:
#         pass
#     return None


# def safe_float(x) -> Optional[float]:
#     try:
#         if x is None:
#             return None
#         s = str(x).strip()
#         if s == "":
#             return None
#         return float(s)
#     except Exception:
#         return None


# def safe_int(x, default=0) -> int:
#     try:
#         if x is None:
#             return default
#         s = str(x).strip()
#         if s == "":
#             return default
#         return int(float(s))
#     except Exception:
#         return default


# def norm_status(raw: str) -> str:
#     """
#     Map crawler geocode_status -> Django choices
#     crawler: OK, NOT_FOUND, EMPTY_LOC, SKIP_NOISE, SKIP_TOO_GENERAL, NET_ERR, TIMEOUT, RATE_LIMIT, SERVICE_ERR
#     django: ok, not_found, empty, skip_noise, skip_general, net_err, timeout, rate_limit, service_err
#     """
#     s = (raw or "").strip().lower()
#     m = {
#         "ok": "ok",
#         "not_found": "not_found",
#         "empty_loc": "empty",
#         "empty": "empty",
#         "skip_noise": "skip_noise",
#         "skip_too_general": "skip_general",
#         "skip_general": "skip_general",
#         "net_err": "net_err",
#         "timeout": "timeout",
#         "rate_limit": "rate_limit",
#         "service_err": "service_err",
#     }
#     return m.get(s, "empty")


# def make_dedup_hash(title: str, final_url: str) -> str:
#     base = (title or "").strip() + "|" + (final_url or "").strip()
#     return hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()


# class Command(BaseCommand):
#     help = "Import crawler CSV (data_intel_raw.csv) into Django DB (Signal + SignalLocation + Source)."

#     def add_arguments(self, parser):
#         parser.add_argument(
#             "--raw",
#             required=True,
#             help="Path to crawler raw CSV, e.g. ..\\crawler\\output\\data_intel_raw.csv"
#         )
#         parser.add_argument(
#             "--dry-run",
#             action="store_true",
#             help="Parse and report counts but do not write to DB."
#         )

#     def handle(self, *args, **options):
#         path = options["raw"]
#         dry = options["dry_run"]

#         try:
#             f = open(path, "r", encoding="utf-8")
#         except Exception as e:
#             raise CommandError(f"Cannot open CSV: {path} ({e})")

#         created_signals = 0
#         skipped_dupe = 0
#         created_locations = 0  # (not used yet; location linking will be next stage)
#         created_sources = 0
#         created_signal_locs = 0

#         with f:
#             reader = csv.DictReader(f)
#             if not reader.fieldnames:
#                 raise CommandError("CSV has no header/fieldnames.")

#             for row in reader:
#                 title = (row.get("judul") or "").strip()
#                 url = (row.get("link") or "").strip()
#                 final_url = (row.get("final_url") or "").strip() or url
#                 sumber = (row.get("sumber") or "Unknown").strip()

#                 disease_tag = (row.get("penyakit_tag") or "").strip() or "Unknown"
#                 threat_score = safe_int(row.get("skor_ancaman"), 0)
#                 summary = (row.get("summary") or "").strip() or None
#                 content_text = None  # kalau nanti kamu simpan full content, taruh di sini

#                 published_at = parse_datetime_any(row.get("tanggal"))
#                 crawled_at = timezone.now()

#                 # dedup_hash: pakai dari CSV kalau ada, kalau tidak generate
#                 dedup_hash = (row.get("dedup_hash") or "").strip()
#                 if not dedup_hash:
#                     dedup_hash = make_dedup_hash(title, final_url)

#                 if Signal.objects.filter(dedup_hash=dedup_hash).exists():
#                     skipped_dupe += 1
#                     continue

#                 # Source
#                 src_obj, src_created = Source.objects.get_or_create(name=sumber)
#                 if src_created:
#                     created_sources += 1

#                 # Geocode fields
#                 raw_loc = (row.get("lokasi_mentah") or "").strip() or None
#                 level = (row.get("level_lokasi") or "").strip().lower() or "unknown"
#                 conf = safe_float(row.get("confidence_lokasi")) or 0.0
#                 geo_status = norm_status(row.get("geocode_status"))

#                 lat = safe_float(row.get("lat"))
#                 lon = safe_float(row.get("lon"))

#                 if dry:
#                     created_signals += 1
#                     created_signal_locs += 1
#                     continue

#                 # Create Signal
#                 sig = Signal.objects.create(
#                     source=src_obj,
#                     disease_tag=disease_tag,
#                     title=title or "(no-title)",
#                     url=url or final_url or "https://news.google.com",
#                     final_url=final_url,
#                     published_at=published_at,
#                     crawled_at=crawled_at,
#                     summary=summary,
#                     content_text=content_text,
#                     threat_score=threat_score,
#                     status="raw",
#                     dedup_hash=dedup_hash,
#                     language="id",
#                 )
#                 created_signals += 1

#                 # Create SignalLocation (primary)
#                 SignalLocation.objects.create(
#                     signal=sig,
#                     location=None,  # tahap berikutnya: link ke Location table
#                     raw_location_text=raw_loc,
#                     method="gazetteer" if conf >= 0.75 else "regex" if conf > 0 else "regex",
#                     confidence=conf,
#                     geocode_status=geo_status,
#                     lat=lat,
#                     lon=lon,
#                     is_primary=True,
#                 )
#                 created_signal_locs += 1

#         msg = (
#             f"Import done. dry_run={dry}\n"
#             f"  created_signals: {created_signals}\n"
#             f"  created_signal_locations: {created_signal_locs}\n"
#             f"  created_sources: {created_sources}\n"
#             f"  skipped_dupe: {skipped_dupe}\n"
#         )
#         self.stdout.write(self.style.SUCCESS(msg))

import csv
import hashlib
from datetime import datetime
from typing import Optional

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from intel.models import Source, Signal, SignalLocation


def parse_datetime_any(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = str(s).strip()
    fmts = [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                return dt
            return dt.astimezone(timezone.get_current_timezone()).replace(tzinfo=None)
        except Exception:
            continue

    try:
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return datetime.strptime(s[:10], "%Y-%m-%d")
    except Exception:
        pass

    return None


def safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        s = str(x).strip()
        if s == "":
            return None
        return float(s)
    except Exception:
        return None


def safe_int(x, default=0) -> int:
    try:
        if x is None:
            return default
        s = str(x).strip()
        if s == "":
            return default
        return int(float(s))
    except Exception:
        return default


def norm_status(raw: str) -> str:
    s = (raw or "").strip().lower()
    m = {
        "ok": "ok",
        "not_found": "not_found",
        "empty_loc": "empty",
        "empty": "empty",
        "skip_noise": "skip_noise",
        "skip_too_general": "skip_general",
        "skip_general": "skip_general",
        "net_err": "net_err",
        "timeout": "timeout",
        "rate_limit": "rate_limit",
        "service_err": "service_err",
    }
    return m.get(s, "empty")


def make_dedup_hash(title: str, final_url: str) -> str:
    base = (title or "").strip() + "|" + (final_url or "").strip()
    return hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()


class Command(BaseCommand):
    help = "Import crawler CSV (data_intel_raw.csv) into Django DB (Signal + SignalLocation + Source)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--raw",
            required=True,
            help="Path to crawler raw CSV, e.g. ..\\crawler\\output\\data_intel_raw.csv"
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report counts but do not write to DB."
        )

    def handle(self, *args, **options):
        path = options["raw"]
        dry = options["dry_run"]

        try:
            f = open(path, "r", encoding="utf-8")
        except Exception as e:
            raise CommandError(f"Cannot open CSV: {path} ({e})")

        created_signals = 0
        skipped_dupe = 0
        created_sources = 0
        created_signal_locs = 0

        with f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise CommandError("CSV has no header/fieldnames.")

            for row in reader:
                title = (row.get("judul") or "").strip()
                url = (row.get("link") or "").strip()
                final_url = (row.get("final_url") or "").strip() or url
                sumber = (row.get("sumber") or "Unknown").strip()

                disease_tag = (row.get("penyakit_tag") or "").strip() or "Unknown"
                detected_diseases = (row.get("detected_diseases") or "").strip() or None
                event_types = (row.get("event_types") or "").strip() or None
                severity_nlp = safe_int(row.get("severity_nlp"), 0)

                threat_score = safe_int(row.get("skor_ancaman"), 0)
                summary = (row.get("summary") or "").strip() or None
                content_text = None

                published_at = parse_datetime_any(row.get("tanggal"))
                crawled_at = timezone.now()

                dedup_hash = (row.get("dedup_hash") or "").strip()
                if not dedup_hash:
                    dedup_hash = make_dedup_hash(title, final_url)

                if Signal.objects.filter(dedup_hash=dedup_hash).exists():
                    skipped_dupe += 1
                    continue

                src_obj, src_created = Source.objects.get_or_create(name=sumber)
                if src_created:
                    created_sources += 1

                raw_loc = (row.get("lokasi_mentah") or "").strip() or None
                conf = safe_float(row.get("confidence_lokasi")) or 0.0
                geo_status = norm_status(row.get("geocode_status"))

                lat = safe_float(row.get("lat"))
                lon = safe_float(row.get("lon"))

                if dry:
                    created_signals += 1
                    created_signal_locs += 1
                    continue

                sig = Signal.objects.create(
                    source=src_obj,
                    disease_tag=disease_tag,
                    detected_diseases=detected_diseases,
                    event_types=event_types,
                    severity_nlp=severity_nlp,
                    title=title or "(no-title)",
                    url=url or final_url or "https://news.google.com",
                    final_url=final_url,
                    published_at=published_at,
                    crawled_at=crawled_at,
                    summary=summary,
                    content_text=content_text,
                    threat_score=threat_score,
                    status="raw",
                    dedup_hash=dedup_hash,
                    language="id",
                )
                created_signals += 1

                method = "gazetteer" if conf >= 0.75 else "regex" if conf > 0 else "regex"

                SignalLocation.objects.create(
                    signal=sig,
                    location=None,
                    raw_location_text=raw_loc,
                    method=method,
                    confidence=conf,
                    geocode_status=geo_status,
                    lat=lat,
                    lon=lon,
                    is_primary=True,
                )
                created_signal_locs += 1

        msg = (
            f"Import done. dry_run={dry}\n"
            f"  created_signals: {created_signals}\n"
            f"  created_signal_locations: {created_signal_locs}\n"
            f"  created_sources: {created_sources}\n"
            f"  skipped_dupe: {skipped_dupe}\n"
        )
        self.stdout.write(self.style.SUCCESS(msg))