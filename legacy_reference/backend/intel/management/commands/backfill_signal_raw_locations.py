from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from intel.models import Signal
from intel.utils.location_extractor import extract_location_from_text


class Command(BaseCommand):
    help = "Backfill Signal.raw_location_text from title/content"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--only-empty", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        qs = Signal.objects.all().order_by("id")

        if options["only_empty"]:
            qs = qs.filter(
                Q(raw_location_text__isnull=True) | Q(raw_location_text="")
            )

        limit = options["limit"]
        if limit and limit > 0:
            qs = qs[:limit]

        dry_run = options["dry_run"]

        total = 0
        filled = 0
        not_found = 0

        for signal in qs:
            result = extract_location_from_text(signal.title or "", signal.content or "")
            total += 1

            if result.raw_location_text:
                old_value = signal.raw_location_text or ""
                signal.raw_location_text = result.raw_location_text

                if not dry_run:
                    signal.save(update_fields=["raw_location_text"])

                filled += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[FILLED] Signal #{signal.id} | "
                        f"{old_value!r} -> {result.raw_location_text!r} | {result.method}"
                    )
                )
            else:
                not_found += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"[NOT FOUND] Signal #{signal.id} | {result.note}"
                    )
                )

        if dry_run:
            transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== RAW LOCATION BACKFILL SUMMARY ==="))
        self.stdout.write(f"Total checked : {total}")
        self.stdout.write(f"Filled        : {filled}")
        self.stdout.write(f"Not found     : {not_found}")
        self.stdout.write(f"Dry run       : {dry_run}")