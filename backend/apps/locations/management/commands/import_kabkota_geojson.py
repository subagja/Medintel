import json
from pathlib import Path
from typing import Any

from django.core.management.base import (
    BaseCommand,
    CommandError,
)
from django.db import transaction

from apps.locations.models import Location


def compute_representative_point(
    geometry: dict[str, Any],
) -> tuple[float | None, float | None]:
    if not geometry:
        return None, None

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []

    points: list[list[float]] = []

    if geometry_type == "Polygon":
        if coordinates:
            points.extend(
                coordinates[0]
            )

    elif geometry_type == "MultiPolygon":
        for polygon in coordinates:
            if polygon:
                points.extend(
                    polygon[0]
                )

    valid_points = [
        point
        for point in points
        if (
            isinstance(point, list)
            and len(point) >= 2
        )
    ]

    if not valid_points:
        return None, None

    longitude = sum(
        float(point[0])
        for point in valid_points
    ) / len(valid_points)

    latitude = sum(
        float(point[1])
        for point in valid_points
    ) / len(valid_points)

    return latitude, longitude


def determine_level(
    value: str,
) -> str | None:
    normalized = (
        value or ""
    ).strip().casefold()

    if normalized in {
        "city",
        "kota",
    }:
        return (
            Location.AdministrativeLevel.CITY
        )

    if normalized in {
        "regency",
        "kabupaten",
        "kab",
    }:
        return (
            Location.AdministrativeLevel.REGENCY
        )

    return None


class Command(BaseCommand):
    help = (
        "Mengimpor master kabupaten/kota "
        "Indonesia dari GeoJSON."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            required=True,
            type=str,
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
        )

        parser.add_argument(
            "--update-existing",
            action="store_true",
        )

    @transaction.atomic
    def handle(
        self,
        *args: Any,
        **options: Any,
    ) -> None:
        file_path = Path(
            options["file"]
        )

        if not file_path.exists():
            raise CommandError(
                f"File tidak ditemukan: {file_path}"
            )

        with file_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        features = data.get(
            "features",
            [],
        )

        if not features:
            raise CommandError(
                "GeoJSON tidak memiliki feature."
            )

        indonesia = Location.objects.filter(
            administrative_level=(
                Location.AdministrativeLevel.COUNTRY
            ),
            country_code="ID",
        ).first()

        if indonesia is None:
            raise CommandError(
                (
                    "Master Indonesia belum tersedia. "
                    "Jalankan import provinsi dahulu."
                )
            )

        created_count = 0
        updated_count = 0
        skipped_count = 0
        missing_parent_count = 0

        for index, feature in enumerate(
            features,
            start=1,
        ):
            properties = (
                feature.get("properties")
                or {}
            )

            geometry = (
                feature.get("geometry")
                or {}
            )

            name = (
                properties.get("display_name")
                or properties.get("name")
                or ""
            ).strip()

            province_name = (
                properties.get("province_name")
                or ""
            ).strip()

            code = (
                properties.get("bps_code")
                or properties.get(
                    "city_regency_code"
                )
                or ""
            ).strip()

            level = determine_level(
                properties.get("level")
                or ""
            )

            if not name or not province_name or not level:
                skipped_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        (
                            f"[SKIP {index}] "
                            f"name={name!r} "
                            f"province={province_name!r} "
                            f"level={level!r}"
                        )
                    )
                )
                continue

            parent = Location.objects.filter(
                administrative_level=(
                    Location.AdministrativeLevel.PROVINCE
                ),
                name__iexact=province_name,
                parent=indonesia,
                country_code="ID",
                is_active=True,
            ).first()

            if parent is None:
                missing_parent_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        (
                            f"[NO PARENT] {name} "
                            f"→ {province_name}"
                        )
                    )
                )
                continue

            latitude, longitude = (
                compute_representative_point(
                    geometry
                )
            )

            location = (
                Location.objects.filter(
                    administrative_level=level,
                    name__iexact=name,
                    parent=parent,
                    country_code="ID",
                ).first()
            )

            if location is not None:
                if not options["update_existing"]:
                    skipped_count += 1
                    self.stdout.write(
                        f"[EXISTS] {name}"
                    )
                    continue

                if not options["dry_run"]:
                    location.code = code
                    location.latitude = latitude
                    location.longitude = longitude
                    location.is_active = True

                    location.save(
                        update_fields=[
                            "code",
                            "latitude",
                            "longitude",
                            "is_active",
                            "updated_at",
                        ]
                    )

                updated_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        (
                            f"[UPDATED] {name} "
                            f"→ {parent.name}"
                        )
                    )
                )
                continue

            if not options["dry_run"]:
                Location.objects.create(
                    name=name,
                    code=code,
                    administrative_level=level,
                    parent=parent,
                    latitude=latitude,
                    longitude=longitude,
                    country_code="ID",
                    is_active=True,
                )

            created_count += 1

            self.stdout.write(
                self.style.SUCCESS(
                    (
                        f"[CREATED] {name} "
                        f"→ {parent.name}"
                    )
                )
            )

        if options["dry_run"]:
            transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "=== RINGKASAN IMPORT KAB/KOTA ==="
            )
        )
        self.stdout.write(
            f"Created        : {created_count}"
        )
        self.stdout.write(
            f"Updated        : {updated_count}"
        )
        self.stdout.write(
            f"Skipped        : {skipped_count}"
        )
        self.stdout.write(
            f"Parent missing : {missing_parent_count}"
        )
        self.stdout.write(
            f"Dry run        : {options['dry_run']}"
        )