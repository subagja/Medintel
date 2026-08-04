from __future__ import annotations

import re

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q

from apps.entities.models import Location, LocationAlias


EXPECTED_COUNTS = {
    Location.AdministrativeLevel.COUNTRY: 1,
    Location.AdministrativeLevel.PROVINCE: 38,
    Location.AdministrativeLevel.REGENCY: 416,
    Location.AdministrativeLevel.CITY: 98,
}

PAPUA_PROVINCE_CODES = {
    "Papua": "91",
    "Papua Barat": "92",
    "Papua Selatan": "93",
    "Papua Tengah": "94",
    "Papua Pegunungan": "95",
    "Papua Barat Daya": "96",
}

MUNICIPALITY_CODE_PATTERN = re.compile(r"^(\d{2})\.(\d{2})$")


def expected_level_from_code(code: str) -> str | None:
    match = MUNICIPALITY_CODE_PATTERN.fullmatch((code or "").strip())
    if match is None:
        return None

    suffix = int(match.group(2))
    if 1 <= suffix <= 69:
        return Location.AdministrativeLevel.REGENCY
    if 71 <= suffix <= 99:
        return Location.AdministrativeLevel.CITY
    return None


class Command(BaseCommand):
    help = (
        "Mengaudit jumlah, kode, parent, dan koordinat master inti "
        "lokasi Indonesia untuk normalisasi artikel."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help=(
                "Gagal bila jumlah belum 38 provinsi, 416 kabupaten, "
                "98 kota atau masih ada koordinat kosong."
            ),
        )

    def handle(self, *args, **options):
        locations = Location.objects.filter(country_code="ID", is_active=True)
        indonesia = locations.filter(
            administrative_level=Location.AdministrativeLevel.COUNTRY,
            parent=None,
        ).first()
        if indonesia is None:
            raise CommandError("Root lokasi Indonesia belum tersedia.")

        level_counts = {
            level: locations.filter(administrative_level=level).count()
            for level, _ in Location.AdministrativeLevel.choices
        }
        core_locations = locations.filter(
            administrative_level__in=[
                Location.AdministrativeLevel.PROVINCE,
                Location.AdministrativeLevel.REGENCY,
                Location.AdministrativeLevel.CITY,
            ]
        ).select_related("parent")
        core_without_coordinates = list(
            core_locations.filter(
                Q(latitude__isnull=True) | Q(longitude__isnull=True)
            ).order_by("administrative_level", "code", "name")
        )
        provinces_with_wrong_parent = list(
            locations.filter(
                administrative_level=Location.AdministrativeLevel.PROVINCE
            )
            .exclude(parent=indonesia)
            .select_related("parent")
        )
        municipalities_with_wrong_parent = list(
            locations.filter(
                administrative_level__in=[
                    Location.AdministrativeLevel.REGENCY,
                    Location.AdministrativeLevel.CITY,
                ]
            )
            .exclude(parent__administrative_level=Location.AdministrativeLevel.PROVINCE)
            .select_related("parent")
        )

        duplicate_code_rows = list(
            locations.exclude(code="")
            .values("code")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
            .order_by("code")
        )
        duplicate_codes = [row["code"] for row in duplicate_code_rows]
        duplicate_locations = list(
            locations.filter(code__in=duplicate_codes)
            .select_related("parent")
            .order_by("code", "administrative_level", "name")
        )

        invalid_codes: list[Location] = []
        level_code_mismatches: list[Location] = []
        for item in core_locations:
            code = (item.code or "").strip()
            if item.administrative_level == Location.AdministrativeLevel.PROVINCE:
                if not re.fullmatch(r"\d{2}", code):
                    invalid_codes.append(item)
                continue

            expected_level = expected_level_from_code(code)
            if expected_level is None:
                invalid_codes.append(item)
            elif expected_level != item.administrative_level:
                level_code_mismatches.append(item)

        papua_code_mismatches = list(
            locations.filter(
                administrative_level=Location.AdministrativeLevel.PROVINCE,
                name__in=PAPUA_PROVINCE_CODES,
            ).exclude(
                **{
                    "code__in": PAPUA_PROVINCE_CODES.values(),
                }
            )
        )
        for name, expected_code in PAPUA_PROVINCE_CODES.items():
            item = locations.filter(
                administrative_level=Location.AdministrativeLevel.PROVINCE,
                name=name,
            ).first()
            if item is not None and item.code != expected_code and item not in papua_code_mismatches:
                papua_code_mismatches.append(item)

        alias_count = LocationAlias.objects.filter(
            location__country_code="ID",
            location__is_active=True,
            is_active=True,
        ).count()

        parent_error_count = len(provinces_with_wrong_parent) + len(
            municipalities_with_wrong_parent
        )
        self.stdout.write(self.style.MIGRATE_HEADING("Audit master geolocation Indonesia"))
        self.stdout.write(
            f"Negara          : {level_counts[Location.AdministrativeLevel.COUNTRY]} "
            f"(target {EXPECTED_COUNTS[Location.AdministrativeLevel.COUNTRY]})"
        )
        self.stdout.write(
            f"Provinsi        : {level_counts[Location.AdministrativeLevel.PROVINCE]} "
            f"(target {EXPECTED_COUNTS[Location.AdministrativeLevel.PROVINCE]})"
        )
        self.stdout.write(
            f"Kabupaten       : {level_counts[Location.AdministrativeLevel.REGENCY]} "
            f"(target {EXPECTED_COUNTS[Location.AdministrativeLevel.REGENCY]})"
        )
        self.stdout.write(
            f"Kota            : {level_counts[Location.AdministrativeLevel.CITY]} "
            f"(target {EXPECTED_COUNTS[Location.AdministrativeLevel.CITY]})"
        )
        self.stdout.write(
            f"Kecamatan       : {level_counts[Location.AdministrativeLevel.DISTRICT]}"
        )
        self.stdout.write(
            f"Desa/Kelurahan  : {level_counts[Location.AdministrativeLevel.VILLAGE]}"
        )
        self.stdout.write(f"Alias aktif     : {alias_count}")
        self.stdout.write(f"Tanpa koordinat : {len(core_without_coordinates)}")
        self.stdout.write(f"Parent salah    : {parent_error_count}")
        self.stdout.write(f"Kode duplikat   : {len(duplicate_code_rows)}")
        self.stdout.write(f"Kode tidak valid: {len(invalid_codes)}")
        self.stdout.write(f"Level/kode salah: {len(level_code_mismatches)}")
        self.stdout.write(f"Kode Papua salah: {len(papua_code_mismatches)}")

        if duplicate_locations:
            self.stdout.write(self.style.WARNING("Detail kode duplikat:"))
            for item in duplicate_locations:
                parent_name = item.parent.name if item.parent else "-"
                self.stdout.write(
                    f"- {item.code} | {item.name} | {item.administrative_level} | "
                    f"parent={parent_name}"
                )

        if core_without_coordinates:
            self.stdout.write(self.style.WARNING("Detail tanpa koordinat:"))
            for item in core_without_coordinates:
                parent_name = item.parent.name if item.parent else "-"
                self.stdout.write(
                    f"- {item.code or '-'} | {item.name} | "
                    f"{item.administrative_level} | parent={parent_name}"
                )

        if invalid_codes or level_code_mismatches or papua_code_mismatches:
            self.stdout.write(self.style.WARNING("Detail kode/level bermasalah:"))
            for item in [*invalid_codes, *level_code_mismatches, *papua_code_mismatches]:
                self.stdout.write(
                    f"- {item.code or '-'} | {item.name} | {item.administrative_level}"
                )

        errors: list[str] = []
        if parent_error_count:
            errors.append("parent wilayah tidak valid")
        if duplicate_code_rows:
            errors.append("kode wilayah duplikat")
        if invalid_codes:
            errors.append("kode wilayah tidak valid")
        if level_code_mismatches:
            errors.append("level tidak sesuai kode")
        if papua_code_mismatches:
            errors.append("kode provinsi Papua belum mutakhir")

        if options["strict"]:
            for level, expected in EXPECTED_COUNTS.items():
                if level_counts[level] != expected:
                    errors.append(
                        f"jumlah {level} {level_counts[level]} (target {expected})"
                    )
            if core_without_coordinates:
                errors.append("koordinat provinsi/kabupaten/kota belum lengkap")

        if errors:
            raise CommandError(
                "Master geolocation belum siap: " + "; ".join(dict.fromkeys(errors)) + "."
            )

        self.stdout.write(
            self.style.SUCCESS("Master geolocation siap untuk normalisasi domestik.")
        )
