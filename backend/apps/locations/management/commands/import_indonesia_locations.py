from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from django.core.management.base import (
    BaseCommand,
    CommandError,
)
from django.db import transaction

from apps.locations.geolocation import clear_geolocation_cache
from apps.locations.models import Location, LocationAlias


COLUMN_ALIASES = {
    "code": (
        "code",
        "kode",
        "kode_wilayah",
        "wilayah_code",
        "id",
    ),
    "name": (
        "name",
        "nama",
        "nama_wilayah",
        "wilayah",
    ),
    "level": (
        "level",
        "administrative_level",
        "level_lokasi",
        "tingkat",
        "jenis",
    ),
    "parent_code": (
        "parent_code",
        "kode_induk",
        "parent",
        "parent_id",
    ),
    "latitude": (
        "latitude",
        "lat",
        "y",
    ),
    "longitude": (
        "longitude",
        "lon",
        "lng",
        "x",
    ),
}

LEVEL_ALIASES = {
    "province": Location.AdministrativeLevel.PROVINCE,
    "provinsi": Location.AdministrativeLevel.PROVINCE,
    "regency": Location.AdministrativeLevel.REGENCY,
    "kabupaten": Location.AdministrativeLevel.REGENCY,
    "kab": Location.AdministrativeLevel.REGENCY,
    "city": Location.AdministrativeLevel.CITY,
    "kota": Location.AdministrativeLevel.CITY,
    "district": Location.AdministrativeLevel.DISTRICT,
    "kecamatan": Location.AdministrativeLevel.DISTRICT,
    "kec": Location.AdministrativeLevel.DISTRICT,
    "village": Location.AdministrativeLevel.VILLAGE,
    "desa": Location.AdministrativeLevel.VILLAGE,
    "kelurahan": Location.AdministrativeLevel.VILLAGE,
    "kel": Location.AdministrativeLevel.VILLAGE,
}

LEVEL_ORDER = {
    Location.AdministrativeLevel.PROVINCE: 1,
    Location.AdministrativeLevel.REGENCY: 2,
    Location.AdministrativeLevel.CITY: 2,
    Location.AdministrativeLevel.DISTRICT: 3,
    Location.AdministrativeLevel.VILLAGE: 4,
}


@dataclass(frozen=True)
class ImportRow:
    line_number: int
    code: str
    name: str
    administrative_level: str
    parent_code: str
    latitude: Decimal | None
    longitude: Decimal | None


def clean_text(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def canonical_code(value: str | None) -> str:
    code = clean_text(value)
    code = re_sub_code_separators(code)
    code = code.strip(".")

    if code.casefold() == "id":
        return "ID"

    parts = code_parts(code)

    return ".".join(parts) if parts else code


def re_sub_code_separators(value: str) -> str:
    result = value.replace("-", ".").replace("/", ".")

    while ".." in result:
        result = result.replace("..", ".")

    return result


def first_value(
    row: dict[str, str],
    logical_name: str,
) -> str:
    normalized_row = {
        clean_text(key).casefold(): value
        for key, value in row.items()
        if key is not None
    }

    for alias in COLUMN_ALIASES[logical_name]:
        value = clean_text(
            normalized_row.get(alias.casefold())
        )

        if value:
            return value

    return ""


def parse_coordinate(value: str) -> Decimal | None:
    cleaned = clean_text(value).replace(",", ".")

    if not cleaned:
        return None

    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(
            f"Koordinat tidak valid: {value!r}"
        ) from exc


def code_parts(code: str) -> list[str]:
    if "." in code:
        return [
            part
            for part in code.split(".")
            if part
        ]

    digits = "".join(
        character
        for character in code
        if character.isdigit()
    )

    if len(digits) == 2:
        return [digits]
    if len(digits) == 4:
        return [digits[:2], digits[2:4]]
    if len(digits) == 6:
        return [digits[:2], digits[2:4], digits[4:6]]
    if len(digits) == 10:
        return [
            digits[:2],
            digits[2:4],
            digits[4:6],
            digits[6:10],
        ]

    return []


def infer_level(
    *,
    raw_level: str,
    code: str,
    name: str,
) -> str:
    normalized_level = clean_text(raw_level).casefold()

    if normalized_level:
        mapped = LEVEL_ALIASES.get(normalized_level)

        if mapped:
            return mapped

        raise ValueError(
            f"Level wilayah tidak dikenali: {raw_level!r}"
        )

    parts = code_parts(code)

    if len(parts) == 1:
        return Location.AdministrativeLevel.PROVINCE

    if len(parts) == 2:
        if name.casefold().startswith("kota "):
            return Location.AdministrativeLevel.CITY

        return Location.AdministrativeLevel.REGENCY

    if len(parts) == 3:
        return Location.AdministrativeLevel.DISTRICT

    if len(parts) == 4:
        return Location.AdministrativeLevel.VILLAGE

    raise ValueError(
        "Level tidak dapat diinferensikan dari kode. "
        "Tambahkan kolom level/tingkat."
    )


def infer_parent_code(
    *,
    code: str,
    raw_parent_code: str,
) -> str:
    explicit = canonical_code(raw_parent_code)

    if explicit:
        return explicit

    parts = code_parts(code)

    if len(parts) <= 1:
        return "ID"

    return ".".join(parts[:-1])


def validate_coordinate_pair(
    *,
    latitude: Decimal | None,
    longitude: Decimal | None,
) -> None:
    if (latitude is None) != (longitude is None):
        raise ValueError(
            "Latitude dan longitude harus diisi berpasangan."
        )

    if latitude is not None and not (
        Decimal("-11.5") <= latitude <= Decimal("6.5")
    ):
        raise ValueError(
            "Latitude berada di luar rentang Indonesia."
        )

    if longitude is not None and not (
        Decimal("94.0") <= longitude <= Decimal("142.5")
    ):
        raise ValueError(
            "Longitude berada di luar rentang Indonesia."
        )


def iter_rows(
    file_path: Path,
) -> Iterable[ImportRow]:
    with file_path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as csv_file:
        reader = csv.DictReader(csv_file)

        if not reader.fieldnames:
            raise CommandError(
                "CSV tidak memiliki header."
            )

        for line_number, row in enumerate(
            reader,
            start=2,
        ):
            code = canonical_code(
                first_value(row, "code")
            )
            name = clean_text(
                first_value(row, "name")
            )

            if not code and not name:
                continue

            if not code or not name:
                raise CommandError(
                    f"Baris {line_number}: kode dan nama wajib diisi."
                )

            try:
                level = infer_level(
                    raw_level=first_value(row, "level"),
                    code=code,
                    name=name,
                )
                parent_code = infer_parent_code(
                    code=code,
                    raw_parent_code=first_value(
                        row,
                        "parent_code",
                    ),
                )
                latitude = parse_coordinate(
                    first_value(row, "latitude")
                )
                longitude = parse_coordinate(
                    first_value(row, "longitude")
                )
                validate_coordinate_pair(
                    latitude=latitude,
                    longitude=longitude,
                )
            except ValueError as exc:
                raise CommandError(
                    f"Baris {line_number}: {exc}"
                ) from exc

            yield ImportRow(
                line_number=line_number,
                code=code,
                name=name,
                administrative_level=level,
                parent_code=parent_code,
                latitude=latitude,
                longitude=longitude,
            )


def alias_candidates(location: Location) -> set[str]:
    name = location.name.strip()

    if (
        location.administrative_level
        == Location.AdministrativeLevel.REGENCY
        and name.casefold().startswith("kabupaten ")
    ):
        suffix = name[len("Kabupaten "):].strip()
        return {
            f"Kab. {suffix}",
            f"Kab {suffix}",
        }

    return set()


class Command(BaseCommand):
    help = (
        "Mengimpor master hierarki wilayah Indonesia dari CSV "
        "berkode wilayah. Mendukung provinsi, kabupaten/kota, "
        "kecamatan, dan desa/kelurahan."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "file_path",
            type=str,
            help="Path CSV master wilayah Indonesia.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validasi tanpa menyimpan perubahan.",
        )
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help=(
                "Perbarui nama, parent, koordinat, dan status "
                "untuk kode wilayah yang sudah ada."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        file_path = Path(options["file_path"])

        if not file_path.exists():
            raise CommandError(
                f"File tidak ditemukan: {file_path}"
            )

        rows = list(iter_rows(file_path))

        if not rows:
            raise CommandError(
                "CSV tidak memiliki baris wilayah yang valid."
            )

        code_counts = Counter(row.code for row in rows)
        duplicate_codes = {
            code
            for code, count in code_counts.items()
            if count > 1
        }

        if duplicate_codes:
            raise CommandError(
                "Kode wilayah duplikat: "
                + ", ".join(sorted(duplicate_codes)[:20])
            )

        rows.sort(
            key=lambda row: (
                LEVEL_ORDER[row.administrative_level],
                row.code,
            )
        )

        indonesia, _ = Location.objects.get_or_create(
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

        if indonesia.code != "ID" or not indonesia.is_active:
            indonesia.code = "ID"
            indonesia.is_active = True
            indonesia.save(
                update_fields=[
                    "code",
                    "is_active",
                    "updated_at",
                ]
            )

        locations_by_code: dict[str, Location] = {
            "ID": indonesia,
        }
        existing_locations = Location.objects.filter(
            country_code="ID",
        ).exclude(
            code="",
        ).exclude(
            pk=indonesia.pk,
        )

        for existing in existing_locations:
            existing_code = canonical_code(existing.code)

            if existing_code in locations_by_code:
                raise CommandError(
                    "Master existing memiliki kode wilayah duplikat: "
                    f"{existing_code}"
                )
            locations_by_code[existing_code] = existing

        created_count = 0
        updated_count = 0
        unchanged_count = 0
        alias_count = 0

        for row in rows:
            parent = locations_by_code.get(row.parent_code)

            if parent is None:
                raise CommandError(
                    f"Baris {row.line_number}: parent code "
                    f"{row.parent_code!r} belum tersedia untuk "
                    f"{row.code} {row.name}."
                )

            location = locations_by_code.get(row.code)

            if location is None:
                location = Location.objects.filter(
                    name__iexact=row.name,
                    administrative_level=(
                        row.administrative_level
                    ),
                    parent=parent,
                    country_code="ID",
                ).first()

            if location is None:
                location = Location.objects.create(
                    name=row.name,
                    code=row.code,
                    administrative_level=(
                        row.administrative_level
                    ),
                    parent=parent,
                    latitude=row.latitude,
                    longitude=row.longitude,
                    country_code="ID",
                    is_active=True,
                )
                created_count += 1
            elif options["update_existing"]:
                changed_fields: list[str] = []

                for field_name, new_value in (
                    ("name", row.name),
                    ("code", row.code),
                    (
                        "administrative_level",
                        row.administrative_level,
                    ),
                    ("parent", parent),
                    ("latitude", row.latitude),
                    ("longitude", row.longitude),
                    ("is_active", True),
                ):
                    if getattr(location, field_name) == new_value:
                        continue

                    setattr(location, field_name, new_value)
                    changed_fields.append(field_name)

                if changed_fields:
                    changed_fields.append("updated_at")
                    location.save(
                        update_fields=changed_fields
                    )
                    updated_count += 1
                else:
                    unchanged_count += 1
            else:
                unchanged_count += 1

            locations_by_code[row.code] = location

            for alias in alias_candidates(location):
                _, alias_created = (
                    LocationAlias.objects.get_or_create(
                        location=location,
                        alias=alias,
                        defaults={
                            "language": "id",
                            "is_active": True,
                        },
                    )
                )
                alias_count += int(alias_created)

        clear_geolocation_cache()

        if options["dry_run"]:
            transaction.set_rollback(True)

        level_counts = {
            level: sum(
                row.administrative_level == level
                for row in rows
            )
            for level in LEVEL_ORDER
        }

        self.stdout.write(
            self.style.SUCCESS(
                "Import master wilayah Indonesia selesai."
            )
        )
        self.stdout.write(
            f"Provinsi       : {level_counts[Location.AdministrativeLevel.PROVINCE]}"
        )
        self.stdout.write(
            "Kabupaten      : "
            f"{level_counts[Location.AdministrativeLevel.REGENCY]}"
        )
        self.stdout.write(
            f"Kota           : {level_counts[Location.AdministrativeLevel.CITY]}"
        )
        self.stdout.write(
            f"Kecamatan      : {level_counts[Location.AdministrativeLevel.DISTRICT]}"
        )
        self.stdout.write(
            f"Desa/Kelurahan : {level_counts[Location.AdministrativeLevel.VILLAGE]}"
        )
        self.stdout.write(f"Dibuat         : {created_count}")
        self.stdout.write(f"Diperbarui     : {updated_count}")
        self.stdout.write(f"Tetap          : {unchanged_count}")
        self.stdout.write(f"Alias baru     : {alias_count}")
        self.stdout.write(f"Dry run        : {options['dry_run']}")
