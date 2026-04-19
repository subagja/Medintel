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


def to_title_name(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return value
    return " ".join(part.capitalize() for part in value.split())


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


def resolve_level(raw_level: str) -> str | None:
    raw = (raw_level or "").strip().lower()

    if raw in {"city", "kota"}:
        return "city"

    if raw in {"regency", "kabupaten", "kab"}:
        return "regency"

    return None


class Command(BaseCommand):
    help = "Import kabupaten/kota from GeoJSON into existing Location table without replacing provinces."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            required=True,
            help="Path to geojson file",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and simulate import without saving changes",
        )
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help="Update existing city/regency records if matched",
        )
        parser.add_argument(
            "--create-missing-province",
            action="store_true",
            help="Create province if not found in existing Location table",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        file_path = Path(options["file"])
        dry_run = options["dry_run"]
        update_existing = options["update_existing"]
        create_missing_province = options["create_missing_province"]

        if not file_path.exists():
            self.stdout.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        features = data.get("features", [])
        if not features:
            self.stdout.write(self.style.ERROR("GeoJSON has no features."))
            return

        # Ambil province existing
        province_qs = Location.objects.filter(level="province", is_active=True)

        province_by_code = {}
        province_by_normalized = {}

        for prov in province_qs:
            prov_code = clean_code(getattr(prov, "province_code", ""))
            prov_norm = normalize_text(getattr(prov, "normalized_name", "") or prov.name)

            if prov_code:
                province_by_code[prov_code.lower()] = prov
            if prov_norm:
                province_by_normalized[prov_norm] = prov

        created_count = 0
        updated_count = 0
        skipped_count = 0
        province_created_count = 0

        for idx, feature in enumerate(features, start=1):
            props = feature.get("properties", {}) or {}
            geom = feature.get("geometry", {}) or {}

            raw_name = (props.get("name") or "").strip()
            raw_display_name = (props.get("display_name") or raw_name).strip()
            raw_level = props.get("level")
            province_name = (props.get("province_name") or "").strip()
            province_code = clean_code(props.get("province_code"))
            city_regency_code = clean_code(props.get("bps_code") or props.get("city_regency_code"))
            country_code = clean_code(props.get("country_code") or "ID").upper()

            level = resolve_level(raw_level)

            if not raw_name or not level:
                skipped_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"[SKIP {idx}] invalid feature: name={raw_name!r}, level={raw_level!r}"
                    )
                )
                continue

            name = to_title_name(raw_name)
            display_name = raw_display_name
            normalized_name = normalize_text(raw_name)
            province_norm = normalize_text(province_name)

            # Cari parent province existing
            parent = None
            if province_code:
                parent = province_by_code.get(province_code.lower())
            if not parent and province_norm:
                parent = province_by_normalized.get(province_norm)

            # Optional: create missing province kalau belum ada
            if not parent and create_missing_province and province_name:
                province_defaults = {
                    "display_name": province_name,
                    "normalized_name": province_norm,
                    "country_code": country_code,
                    "province_code": province_code.lower() if province_code else province_norm,
                    "is_active": True,
                }

                if not dry_run:
                    parent, prov_created = Location.objects.update_or_create(
                        level="province",
                        normalized_name=province_norm,
                        defaults=province_defaults,
                    )
                    if prov_created:
                        province_created_count += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"[PROVINCE CREATED] {province_name}"
                            )
                        )
                    province_by_normalized[province_norm] = parent
                    if province_code:
                        province_by_code[province_code.lower()] = parent

            if not parent:
                skipped_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"[SKIP {idx}] province not found for {display_name} | "
                        f"province_name={province_name!r} province_code={province_code!r}"
                    )
                )
                continue

            lat, lon = compute_centroid(geom)

            defaults = {
                "name": name,
                "display_name": display_name,
                "normalized_name": normalized_name,
                "level": level,
                "parent": parent,
                "country_code": country_code,
                "province_code": parent.province_code or province_code.lower() if province_code else "",
                "city_regency_code": city_regency_code,
                "lat": lat,
                "lon": lon,
                "is_active": True,
            }

            # 1. Prioritas match by city_regency_code
            existing = None
            if city_regency_code:
                existing = Location.objects.filter(
                    level=level,
                    city_regency_code=city_regency_code,
                ).first()

            # 2. Fallback: normalized_name + parent + level
            if not existing:
                existing = Location.objects.filter(
                    level=level,
                    normalized_name=normalized_name,
                    parent=parent,
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
                            f"[UPDATED] {display_name} ({level}) -> {parent.display_name or parent.name}"
                        )
                    )
                else:
                    skipped_count += 1
                    self.stdout.write(
                        f"[EXISTS] {display_name} ({level}) -> {parent.display_name or parent.name}"
                    )
                continue

            if dry_run:
                created_count += 1
                self.stdout.write(
                    f"[DRY RUN CREATE] {display_name} ({level}) -> {parent.display_name or parent.name}"
                )
                continue

            Location.objects.create(**defaults)
            created_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"[CREATED] {display_name} ({level}) -> {parent.display_name or parent.name}"
                )
            )

        if dry_run:
            transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== IMPORT SUMMARY ==="))
        self.stdout.write(f"Created           : {created_count}")
        self.stdout.write(f"Updated           : {updated_count}")
        self.stdout.write(f"Skipped           : {skipped_count}")
        self.stdout.write(f"Province created  : {province_created_count}")
        self.stdout.write(f"Dry run           : {dry_run}")