import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from intel.models import Location


def normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("&", " dan ")
    value = value.replace("/", " ")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def clean_code(value: str) -> str:
    return (value or "").strip()


def compute_centroid(geometry):
    """
    Return (lat, lon) using simple average of polygon ring points.
    Cukup untuk titik representatif admin area.
    """
    if not geometry:
        return None, None

    geom_type = geometry.get("type")
    coords = geometry.get("coordinates", [])

    xs = []
    ys = []

    if geom_type == "Polygon":
        if coords and coords[0]:
            for point in coords[0]:
                xs.append(point[0])  # lon
                ys.append(point[1])  # lat

    elif geom_type == "MultiPolygon":
        for poly in coords:
            if poly and poly[0]:
                for point in poly[0]:
                    xs.append(point[0])  # lon
                    ys.append(point[1])  # lat

    if not xs or not ys:
        return None, None

    lon = sum(xs) / len(xs)
    lat = sum(ys) / len(ys)

    return lat, lon


class Command(BaseCommand):
    help = "Import/update province Location from GeoJSON and fill province centroid lat/lon."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            required=True,
            help="Path to province geojson file",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and simulate import without saving changes",
        )
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help="Update existing province records if matched",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        file_path = Path(options["file"])
        dry_run = options["dry_run"]
        update_existing = options["update_existing"]

        if not file_path.exists():
            self.stdout.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        features = data.get("features", [])
        if not features:
            self.stdout.write(self.style.ERROR("GeoJSON has no features."))
            return

        created_count = 0
        updated_count = 0
        skipped_count = 0

        for idx, feature in enumerate(features, start=1):
            props = feature.get("properties", {}) or {}
            geom = feature.get("geometry", {}) or {}

            raw_name = (
                props.get("display_name")
                or props.get("name")
                or props.get("province_name")
                or props.get("PROVINSI")
                or props.get("NAME_1")
                or ""
            ).strip()

            raw_code = clean_code(
                props.get("province_code")
                or props.get("region_key")
                or ""
            )

            country_code = clean_code(props.get("country_code") or "ID").upper()

            if not raw_name:
                skipped_count += 1
                self.stdout.write(
                    self.style.WARNING(f"[SKIP {idx}] province name empty")
                )
                continue

            normalized_name = normalize_text(raw_name)

            # Jangan pakai bps_code untuk kode sistem.
            # Jangan percaya province_code GeoJSON kalau ada karakter rusak seperti sulawe_iutara.
            # Kode sistem dibuat dari nama provinsi agar konsisten dengan thematic key.
            province_code = normalized_name

            lat, lon = compute_centroid(geom)

            defaults = {
                "name": raw_name,
                "display_name": raw_name,
                "normalized_name": normalized_name,
                "level": "province",
                "country_code": country_code,
                "province_code": province_code,
                "city_regency_code": "",
                "lat": lat,
                "lon": lon,
                "is_active": True,
                "is_false_positive": False,
            }

            existing = None

            if province_code:
                existing = Location.objects.filter(
                    level="province",
                    province_code=province_code,
                ).first()

            if not existing:
                existing = Location.objects.filter(
                    level="province",
                    normalized_name=normalized_name,
                ).first()

            # Fallback untuk data lama yang province_code-nya masih BPS atau slug rusak dari GeoJSON
            if not existing and raw_code:
                existing = Location.objects.filter(
                    level="province",
                    province_code=normalize_text(raw_code),
                ).first()

            bps_code = clean_code(props.get("bps_code"))
            if not existing and bps_code:
                existing = Location.objects.filter(
                    level="province",
                    province_code=bps_code,
                ).first()

            if existing:
                if update_existing:
                    if not dry_run:
                        for field, value in defaults.items():
                            setattr(existing, field, value)
                        existing.save()

                    updated_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[UPDATED] {raw_name} | code={province_code} | lat={lat} lon={lon}"
                        )
                    )
                else:
                    skipped_count += 1
                    self.stdout.write(
                        f"[EXISTS] {raw_name} | use --update-existing to update"
                    )
                continue

            if dry_run:
                created_count += 1
                self.stdout.write(
                    f"[DRY RUN CREATE] {raw_name} | code={province_code} | lat={lat} lon={lon}"
                )
                continue

            Location.objects.create(**defaults)
            created_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"[CREATED] {raw_name} | code={province_code} | lat={lat} lon={lon}"
                )
            )

        if dry_run:
            transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== PROVINCE IMPORT SUMMARY ==="))
        self.stdout.write(f"Created : {created_count}")
        self.stdout.write(f"Updated : {updated_count}")
        self.stdout.write(f"Skipped : {skipped_count}")
        self.stdout.write(f"Dry run : {dry_run}")