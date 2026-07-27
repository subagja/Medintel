from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from intel.models import Location


def score_location(loc):
    """
    Score lebih tinggi = lebih layak dipertahankan.
    Prioritas:
    1. normalized_name pakai underscore
    2. punya lat/lon
    3. id lebih kecil
    """
    score = 0

    norm = (loc.normalized_name or "").strip()
    if "_" in norm:
        score += 10
    if norm and " " not in norm:
        score += 5
    if loc.lat is not None and loc.lon is not None:
        score += 3

    # id kecil lebih diprioritaskan
    score += max(0, 100000 - loc.id) / 100000

    return score


class Command(BaseCommand):
    help = "Deduplicate province Location rows by province_code"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview only, do not delete anything",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        provinces = list(
            Location.objects.filter(level="province").exclude(province_code="")
        )

        grouped = defaultdict(list)
        for loc in provinces:
            grouped[(loc.province_code or "").strip().lower()].append(loc)

        total_groups = 0
        total_deleted = 0

        for province_code, items in grouped.items():
            if len(items) <= 1:
                continue

            total_groups += 1
            items_sorted = sorted(items, key=score_location, reverse=True)
            keep = items_sorted[0]
            duplicates = items_sorted[1:]

            self.stdout.write("")
            self.stdout.write(self.style.WARNING(f"[DUPLICATE] province_code={province_code}"))
            self.stdout.write(f"  KEEP   -> id={keep.id}, name={keep.name}, normalized_name={keep.normalized_name}")

            for dup in duplicates:
                self.stdout.write(f"  DELETE -> id={dup.id}, name={dup.name}, normalized_name={dup.normalized_name}")

                # Re-point children if any
                children = Location.objects.filter(parent=dup)
                if children.exists():
                    self.stdout.write(f"           re-parent {children.count()} child rows to id={keep.id}")
                    if not dry_run:
                        children.update(parent=keep)

                if not dry_run:
                    dup.delete()
                total_deleted += 1

        if dry_run:
            transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== DEDUP SUMMARY ==="))
        self.stdout.write(f"Duplicate groups : {total_groups}")
        self.stdout.write(f"Deleted rows     : {total_deleted}")
        self.stdout.write(f"Dry run          : {dry_run}")