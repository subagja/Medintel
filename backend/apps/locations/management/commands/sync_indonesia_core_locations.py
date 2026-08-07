from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.locations.geolocation import clear_geolocation_cache
from apps.entities.models import (
    ArticleFact,
    ArticleLocation,
    ValidationStatus,
)
from apps.locations.models import Location, LocationAlias


REFERENCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "indonesia_core_locations_2025.csv"
)

EXPECTED_COUNTS = {
    Location.AdministrativeLevel.PROVINCE: 38,
    Location.AdministrativeLevel.REGENCY: 416,
    Location.AdministrativeLevel.CITY: 98,
}

LEVEL_MAP = {
    "province": Location.AdministrativeLevel.PROVINCE,
    "regency": Location.AdministrativeLevel.REGENCY,
    "city": Location.AdministrativeLevel.CITY,
}

SPECIAL_NORMALIZED_NAMES = {
    "kota baru": "kotabaru",
    "kota waringin barat": "kotawaringin barat",
    "kota waringin timur": "kotawaringin timur",
}

ADMINISTRATIVE_PREFIXES = (
    "kabupaten administrasi ",
    "kota administrasi ",
    "kabupaten ",
    "kab. ",
    "kab ",
    "kota ",
    "provinsi ",
)


@dataclass(frozen=True)
class ReferenceLocation:
    code: str
    name: str
    level: str
    parent_code: str
    latitude: Decimal
    longitude: Decimal


def clean_text(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def normalized_name(value: str) -> str:
    normalized = clean_text(value).casefold()
    normalized = re.sub(r"[^\w\s]+", " ", normalized)
    normalized = " ".join(normalized.split())
    normalized = SPECIAL_NORMALIZED_NAMES.get(normalized, normalized)

    for prefix in ADMINISTRATIVE_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
            break

    return normalized


def load_reference() -> tuple[ReferenceLocation, ...]:
    if not REFERENCE_PATH.exists():
        raise CommandError(
            f"Reference master tidak ditemukan: {REFERENCE_PATH}"
        )

    rows: list[ReferenceLocation] = []
    with REFERENCE_PATH.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as source:
        for line_number, row in enumerate(csv.DictReader(source), start=2):
            try:
                level = LEVEL_MAP[clean_text(row.get("level"))]
                rows.append(
                    ReferenceLocation(
                        code=clean_text(row.get("code")),
                        name=clean_text(row.get("name")),
                        level=level,
                        parent_code=clean_text(row.get("parent_code")),
                        latitude=Decimal(clean_text(row.get("latitude"))),
                        longitude=Decimal(clean_text(row.get("longitude"))),
                    )
                )
            except (KeyError, ArithmeticError) as exc:
                raise CommandError(
                    f"Reference baris {line_number} tidak valid."
                ) from exc

    codes = [row.code for row in rows]
    if len(codes) != len(set(codes)):
        raise CommandError("Reference memiliki kode wilayah duplikat.")

    actual_counts = {
        level: sum(row.level == level for row in rows)
        for level in EXPECTED_COUNTS
    }
    if actual_counts != EXPECTED_COUNTS:
        raise CommandError(
            "Jumlah reference tidak sesuai acuan: "
            f"{actual_counts}."
        )

    return tuple(rows)


def alias_candidates(reference: ReferenceLocation) -> set[str]:
    name = reference.name
    aliases = {name}

    if reference.level == Location.AdministrativeLevel.REGENCY:
        suffix = re.sub(
            r"^Kabupaten(?: Administrasi)?\s+",
            "",
            name,
            flags=re.IGNORECASE,
        ).strip()
        aliases.update({suffix, f"Kab. {suffix}", f"Kab {suffix}"})

    elif reference.level == Location.AdministrativeLevel.CITY:
        suffix = re.sub(
            r"^Kota(?: Administrasi)?\s+",
            "",
            name,
            flags=re.IGNORECASE,
        ).strip()
        aliases.update({suffix, f"Kota {suffix}"})

    return {alias for alias in aliases if alias and alias != name}


def select_existing(
    *,
    reference: ReferenceLocation,
    parent: Location,
) -> tuple[Location | None, tuple[Location, ...]]:
    candidates = list(
        Location.objects.filter(
            country_code="ID",
            parent=parent,
        ).filter(
            administrative_level__in=[
                Location.AdministrativeLevel.PROVINCE,
                Location.AdministrativeLevel.REGENCY,
                Location.AdministrativeLevel.CITY,
            ]
        )
    )
    matching_name = [
        item
        for item in candidates
        if normalized_name(item.name) == normalized_name(reference.name)
    ]

    if not matching_name:
        return None, ()

    def duplicates_for(selected: Location) -> tuple[Location, ...]:
        return tuple(
            item
            for item in matching_name
            if item.pk != selected.pk
            and (
                item.administrative_level == reference.level
                or clean_text(item.code) == reference.code
            )
        )

    canonical_name = [
        item
        for item in matching_name
        if clean_text(item.name).casefold()
        == clean_text(reference.name).casefold()
        and item.administrative_level == reference.level
    ]
    if len(canonical_name) == 1:
        selected = canonical_name[0]
        return selected, duplicates_for(selected)

    matching_code = [
        item for item in matching_name if clean_text(item.code) == reference.code
    ]
    if len(matching_code) == 1:
        selected = matching_code[0]
        return selected, duplicates_for(selected)

    matching_level = [
        item
        for item in matching_name
        if item.administrative_level == reference.level
    ]
    if len(matching_level) == 1:
        selected = matching_level[0]
        return selected, duplicates_for(selected)

    if len(matching_name) == 1:
        return None, ()

    names = ", ".join(
        f"{item.name} [{item.code or '-'}; {item.administrative_level}]"
        for item in matching_name
    )
    raise CommandError(
        "Kandidat lokasi ambigu untuk "
        f"{reference.code} {reference.name}: {names}."
    )


VALIDATION_PRIORITY = {
    ValidationStatus.UNREVIEWED: 0,
    ValidationStatus.REJECTED: 1,
    ValidationStatus.VALIDATED: 2,
    ValidationStatus.CORRECTED: 3,
}


def merge_article_location(
    *,
    source_relation: ArticleLocation,
    target_location: Location,
) -> None:
    target_relation = ArticleLocation.objects.filter(
        article_id=source_relation.article_id,
        location=target_location,
    ).first()
    if target_relation is None:
        source_relation.location = target_location
        source_relation.save(update_fields=["location", "updated_at"])
        return

    changed_fields: list[str] = []
    if source_relation.is_primary and not target_relation.is_primary:
        target_relation.is_primary = True
        changed_fields.append("is_primary")

    source_confidence = source_relation.confidence_score
    target_confidence = target_relation.confidence_score
    if source_confidence is not None and (
        target_confidence is None or source_confidence > target_confidence
    ):
        target_relation.confidence_score = source_confidence
        changed_fields.append("confidence_score")

    if not target_relation.mention_text and source_relation.mention_text:
        target_relation.mention_text = source_relation.mention_text
        changed_fields.append("mention_text")

    if VALIDATION_PRIORITY.get(source_relation.validation_status, 0) > (
        VALIDATION_PRIORITY.get(target_relation.validation_status, 0)
    ):
        for field_name in (
            "validation_status",
            "validation_notes",
            "validated_by",
            "validated_at",
        ):
            setattr(
                target_relation,
                field_name,
                getattr(source_relation, field_name),
            )
            changed_fields.append(field_name)

    if changed_fields:
        target_relation.save(
            update_fields=[*dict.fromkeys(changed_fields), "updated_at"]
        )
    source_relation.delete()


def merge_location_into(
    *,
    duplicate: Location,
    canonical: Location,
) -> tuple[int, int, int]:
    """Move dependent data to canonical and deactivate the duplicate row."""
    if duplicate.pk == canonical.pk:
        return 0, 0, 0

    aliases_created = 0
    article_links_moved = 0
    facts_moved = 0

    for alias in duplicate.aliases.all():
        _, was_created = LocationAlias.objects.get_or_create(
            location=canonical,
            alias=alias.alias,
            defaults={
                "language": alias.language,
                "is_active": alias.is_active,
            },
        )
        aliases_created += int(was_created)

    for relation in list(
        ArticleLocation.objects.filter(location=duplicate)
    ):
        merge_article_location(
            source_relation=relation,
            target_location=canonical,
        )
        article_links_moved += 1

    facts_moved = ArticleFact.objects.filter(location=duplicate).update(
        location=canonical
    )

    for child in list(duplicate.children.all()):
        conflicting_child = Location.objects.filter(
            name=child.name,
            administrative_level=child.administrative_level,
            parent=canonical,
            country_code=child.country_code,
        ).exclude(pk=child.pk).first()
        if conflicting_child is None:
            child.parent = canonical
            child.save(update_fields=["parent", "updated_at"])
            continue
        child_aliases, child_links, child_facts = merge_location_into(
            duplicate=child,
            canonical=conflicting_child,
        )
        aliases_created += child_aliases
        article_links_moved += child_links
        facts_moved += child_facts

    if duplicate.is_active:
        duplicate.is_active = False
        duplicate.save(update_fields=["is_active", "updated_at"])

    return aliases_created, article_links_moved, facts_moved


class Command(BaseCommand):
    help = (
        "Menyinkronkan master inti 38 provinsi, 416 kabupaten, dan "
        "98 kota Indonesia tanpa menghapus relasi artikel."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Simpan perubahan. Tanpa opsi ini command hanya dry-run.",
        )
        parser.add_argument(
            "--deactivate-unmatched",
            action="store_true",
            help=(
                "Nonaktifkan provinsi/kabupaten/kota aktif yang tidak "
                "terdapat pada reference. Data tidak dihapus."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        references = load_reference()
        apply_changes = bool(options["apply"])

        indonesia, _ = Location.objects.get_or_create(
            name="Indonesia",
            administrative_level=Location.AdministrativeLevel.COUNTRY,
            parent=None,
            country_code="ID",
            defaults={"code": "ID", "is_active": True},
        )
        root_changes: list[str] = []
        if indonesia.code != "ID":
            indonesia.code = "ID"
            root_changes.append("code")
        if not indonesia.is_active:
            indonesia.is_active = True
            root_changes.append("is_active")
        if root_changes:
            indonesia.save(update_fields=[*root_changes, "updated_at"])

        locations_by_code: dict[str, Location] = {"ID": indonesia}
        referenced_ids: set[str] = {str(indonesia.pk)}
        created = 0
        updated = 0
        unchanged = 0
        aliases_created = 0
        merged = 0
        article_links_moved = 0
        facts_moved = 0

        ordered_references = sorted(
            references,
            key=lambda item: (item.level != Location.AdministrativeLevel.PROVINCE, item.code),
        )

        for reference in ordered_references:
            parent = locations_by_code.get(reference.parent_code)
            if parent is None:
                raise CommandError(
                    f"Parent {reference.parent_code} belum tersedia untuk "
                    f"{reference.code} {reference.name}."
                )

            location, duplicate_candidates = select_existing(
                reference=reference,
                parent=parent,
            )
            if location is None:
                location = Location.objects.create(
                    name=reference.name,
                    code=reference.code,
                    administrative_level=reference.level,
                    parent=parent,
                    latitude=reference.latitude,
                    longitude=reference.longitude,
                    country_code="ID",
                    is_active=True,
                )
                created += 1
            else:
                changed_fields: list[str] = []
                for field_name, value in (
                    ("name", reference.name),
                    ("code", reference.code),
                    ("administrative_level", reference.level),
                    ("parent", parent),
                    ("latitude", reference.latitude),
                    ("longitude", reference.longitude),
                    ("country_code", "ID"),
                    ("is_active", True),
                ):
                    if getattr(location, field_name) == value:
                        continue
                    setattr(location, field_name, value)
                    changed_fields.append(field_name)

                if changed_fields:
                    location.save(update_fields=[*changed_fields, "updated_at"])
                    updated += 1
                else:
                    unchanged += 1

            for duplicate in duplicate_candidates:
                (
                    merged_aliases,
                    merged_article_links,
                    merged_facts,
                ) = merge_location_into(
                    duplicate=duplicate,
                    canonical=location,
                )
                aliases_created += merged_aliases
                article_links_moved += merged_article_links
                facts_moved += merged_facts
                merged += 1

            referenced_ids.add(str(location.pk))
            locations_by_code[reference.code] = location

            for alias in alias_candidates(reference):
                _, was_created = LocationAlias.objects.get_or_create(
                    location=location,
                    alias=alias,
                    defaults={"language": "id", "is_active": True},
                )
                aliases_created += int(was_created)

        unmatched = Location.objects.filter(
            country_code="ID",
            is_active=True,
            administrative_level__in=EXPECTED_COUNTS,
        ).exclude(pk__in=referenced_ids)
        unmatched_rows = list(
            unmatched.select_related("parent").order_by(
                "administrative_level", "code", "name"
            )
        )

        deactivated = 0
        if options["deactivate_unmatched"] and unmatched_rows:
            deactivated = unmatched.update(is_active=False)

        clear_geolocation_cache()

        if not apply_changes:
            transaction.set_rollback(True)

        self.stdout.write(
            self.style.MIGRATE_HEADING("Sinkronisasi master inti Indonesia")
        )
        self.stdout.write("Reference       : 38 provinsi, 416 kabupaten, 98 kota")
        self.stdout.write(f"Dibuat          : {created}")
        self.stdout.write(f"Diperbarui      : {updated}")
        self.stdout.write(f"Tetap           : {unchanged}")
        self.stdout.write(f"Alias baru      : {aliases_created}")
        self.stdout.write(f"Duplikat gabung : {merged}")
        self.stdout.write(f"Relasi dipindah : {article_links_moved}")
        self.stdout.write(f"Fakta dipindah  : {facts_moved}")
        self.stdout.write(f"Di luar acuan   : {len(unmatched_rows)}")
        self.stdout.write(f"Dinonaktifkan   : {deactivated}")
        self.stdout.write(f"Perubahan simpan: {apply_changes}")

        if unmatched_rows:
            self.stdout.write(self.style.WARNING("Detail lokasi di luar acuan:"))
            for item in unmatched_rows[:30]:
                parent_name = item.parent.name if item.parent else "-"
                self.stdout.write(
                    f"- {item.code or '-'} | {item.name} | "
                    f"{item.administrative_level} | parent={parent_name}"
                )

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN: database tidak berubah. Jalankan kembali dengan --apply."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("Sinkronisasi master inti selesai.")
            )
