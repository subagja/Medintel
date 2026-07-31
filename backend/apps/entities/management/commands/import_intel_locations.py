import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.core.management.base import (
    BaseCommand,
    CommandError,
)
from django.db import transaction

from apps.entities.models import Location


LEVEL_MAPPING = {
    "country": (
        Location.AdministrativeLevel.COUNTRY
    ),
    "province": (
        Location.AdministrativeLevel.PROVINCE
    ),
    "regency": (
        Location.AdministrativeLevel.REGENCY
    ),
    "city": (
        Location.AdministrativeLevel.CITY
    ),
    "district": (
        Location.AdministrativeLevel.DISTRICT
    ),
    "village": (
        Location.AdministrativeLevel.VILLAGE
    ),
}


def clean_text(
    value: str | None,
) -> str:
    return " ".join(
        (value or "").strip().split()
    )


def parse_coordinate(
    value: str | None,
) -> Decimal | None:
    cleaned = clean_text(value)

    if not cleaned:
        return None

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


class Command(BaseCommand):
    help = (
        "Mengimpor lokasi unik dari data_intel_geo.csv "
        "ke master Location."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "file_path",
            type=str,
            help=(
                "Lokasi file data_intel_geo.csv."
            ),
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Memvalidasi file tanpa menyimpan data."
            ),
        )

    @transaction.atomic
    def handle(
        self,
        *args: Any,
        **options: Any,
    ) -> None:
        file_path = Path(
            options["file_path"]
        )

        if not file_path.exists():
            raise CommandError(
                f"File tidak ditemukan: {file_path}"
            )

        indonesia, _ = (
            Location.objects.get_or_create(
                name="Indonesia",
                administrative_level=(
                    Location.AdministrativeLevel.COUNTRY
                ),
                parent=None,
                country_code="ID",
                defaults={
                    "code": "ID",
                    "is_active": True,
                },
            )
        )

        unique_locations: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}

        skipped_status = 0
        skipped_empty = 0
        skipped_level = 0
        invalid_coordinate = 0

        with file_path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as csv_file:
            reader = csv.DictReader(
                csv_file
            )

            required_columns = {
                "lokasi_mentah",
                "level_lokasi",
                "admin_province",
                "admin_kabkota",
                "geocode_status",
                "lat",
                "lon",
            }

            missing_columns = (
                required_columns
                - set(reader.fieldnames or [])
            )

            if missing_columns:
                raise CommandError(
                    (
                        "Kolom CSV belum lengkap: "
                        + ", ".join(
                            sorted(missing_columns)
                        )
                    )
                )

            for line_number, row in enumerate(
                reader,
                start=2,
            ):
                geocode_status = clean_text(
                    row.get("geocode_status")
                ).upper()

                if geocode_status != "OK":
                    skipped_status += 1
                    continue

                raw_level = clean_text(
                    row.get("level_lokasi")
                ).casefold()

                administrative_level = (
                    LEVEL_MAPPING.get(
                        raw_level
                    )
                )

                if administrative_level is None:
                    skipped_level += 1
                    continue

                if (
                    administrative_level
                    == Location.AdministrativeLevel.PROVINCE
                ):
                    location_name = clean_text(
                        row.get("admin_province")
                        or row.get("lokasi_mentah")
                    )

                    parent = indonesia

                elif administrative_level in {
                    Location.AdministrativeLevel.REGENCY,
                    Location.AdministrativeLevel.CITY,
                }:
                    location_name = clean_text(
                        row.get("admin_kabkota")
                        or row.get("lokasi_mentah")
                    )

                    province_name = clean_text(
                        row.get("admin_province")
                    )

                    if not province_name:
                        skipped_empty += 1
                        continue

                    parent, _ = (
                        Location.objects.get_or_create(
                            name=province_name,
                            administrative_level=(
                                Location.AdministrativeLevel.PROVINCE
                            ),
                            parent=indonesia,
                            country_code="ID",
                            defaults={
                                "is_active": True,
                            },
                        )
                    )

                else:
                    location_name = clean_text(
                        row.get("lokasi_mentah")
                    )

                    parent = None

                if not location_name:
                    skipped_empty += 1
                    continue

                latitude = parse_coordinate(
                    row.get("lat")
                )
                longitude = parse_coordinate(
                    row.get("lon")
                )

                if (
                    latitude is None
                    or longitude is None
                ):
                    invalid_coordinate += 1
                    continue

                key = (
                    location_name.casefold(),
                    administrative_level,
                )

                existing = unique_locations.get(
                    key
                )

                if existing is None:
                    unique_locations[key] = {
                        "name": location_name,
                        "administrative_level": (
                            administrative_level
                        ),
                        "parent": parent,
                        "latitude": latitude,
                        "longitude": longitude,
                        "source_line": line_number,
                    }
                    continue

                # Apabila lokasi yang sama muncul berulang,
                # gunakan koordinat pertama dan laporkan bila berbeda.
                if (
                    existing["latitude"]
                    != latitude
                    or existing["longitude"]
                    != longitude
                ):
                    self.stdout.write(
                        self.style.WARNING(
                            (
                                "Koordinat berbeda untuk "
                                f"{location_name}: "
                                f"baris {existing['source_line']} "
                                f"dan {line_number}. "
                                "Koordinat pertama digunakan."
                            )
                        )
                    )

        self.stdout.write(
            (
                "Lokasi unik valid: "
                f"{len(unique_locations)}"
            )
        )

        if options["dry_run"]:
            for item in unique_locations.values():
                self.stdout.write(
                    (
                        f"{item['name']} | "
                        f"{item['administrative_level']} | "
                        f"{item['latitude']}, "
                        f"{item['longitude']}"
                    )
                )

            self.stdout.write(
                self.style.SUCCESS(
                    "Dry-run selesai tanpa menyimpan data."
                )
            )

            transaction.set_rollback(
                True
            )
            return

        created_count = 0
        updated_count = 0
        unchanged_count = 0

        for item in unique_locations.values():
            location, created = (
                Location.objects.get_or_create(
                    name=item["name"],
                    administrative_level=(
                        item["administrative_level"]
                    ),
                    parent=item["parent"],
                    country_code="ID",
                    defaults={
                        "latitude": item["latitude"],
                        "longitude": item["longitude"],
                        "is_active": True,
                    },
                )
            )

            if created:
                created_count += 1
                continue

            changed_fields: list[str] = []

            if (
                location.latitude
                != item["latitude"]
            ):
                location.latitude = (
                    item["latitude"]
                )
                changed_fields.append(
                    "latitude"
                )

            if (
                location.longitude
                != item["longitude"]
            ):
                location.longitude = (
                    item["longitude"]
                )
                changed_fields.append(
                    "longitude"
                )

            if not location.is_active:
                location.is_active = True
                changed_fields.append(
                    "is_active"
                )

            if changed_fields:
                changed_fields.append(
                    "updated_at"
                )

                location.save(
                    update_fields=changed_fields
                )

                updated_count += 1
            else:
                unchanged_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Import lokasi intel selesai."
            )
        )

        self.stdout.write(
            f"Lokasi baru          : {created_count}"
        )
        self.stdout.write(
            f"Lokasi diperbarui    : {updated_count}"
        )
        self.stdout.write(
            f"Tanpa perubahan      : {unchanged_count}"
        )
        self.stdout.write(
            f"Status bukan OK      : {skipped_status}"
        )
        self.stdout.write(
            f"Lokasi kosong        : {skipped_empty}"
        )
        self.stdout.write(
            f"Level tidak didukung : {skipped_level}"
        )
        self.stdout.write(
            f"Koordinat tidak valid: {invalid_coordinate}"
        )