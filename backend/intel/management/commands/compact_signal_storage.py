import os
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from intel.models import Signal


DEFAULT_CONTENT_MAX = int(os.getenv("SIGNAL_CONTENT_MAX_CHARS", "5000"))
DEFAULT_ASSESSMENT_MAX = int(os.getenv("ASSESSMENT_SOURCE_TEXT_MAX_CHARS", "5000"))
TRUNCATED_MARKER = "\n\n[TRUNCATED_FOR_STORAGE]"


def compact_text(value, max_chars):
    value = value or ""
    if max_chars <= 0 or len(value) <= max_chars:
        return value, 0

    compacted = value[:max_chars].rstrip() + TRUNCATED_MARKER
    saved_chars = max(0, len(value) - len(compacted))
    return compacted, saved_chars


class Command(BaseCommand):
    help = "Compact old Signal text payloads and optionally delete stale noise/raw signals."

    def add_arguments(self, parser):
        parser.add_argument("--content-days", type=int, default=14)
        parser.add_argument("--content-max", type=int, default=DEFAULT_CONTENT_MAX)
        parser.add_argument("--assessment-max", type=int, default=DEFAULT_ASSESSMENT_MAX)
        parser.add_argument("--batch-size", type=int, default=500)

        parser.add_argument("--delete-noise", action="store_true")
        parser.add_argument("--noise-days", type=int, default=30)

        parser.add_argument("--delete-raw", action="store_true")
        parser.add_argument("--raw-days", type=int, default=90)

        parser.add_argument("--vacuum", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        now = timezone.now()
        content_cutoff = now - timedelta(days=options["content_days"])
        noise_cutoff = now - timedelta(days=options["noise_days"])
        raw_cutoff = now - timedelta(days=options["raw_days"])

        content_max = options["content_max"]
        assessment_max = options["assessment_max"]
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        compact_qs = (
            Signal.objects
            .filter(created_at__lt=content_cutoff)
            .only("id", "content", "assessment_source_text")
            .order_by("id")
        )

        scanned = 0
        compacted = 0
        saved_chars = 0
        pending_updates = []

        for signal in compact_qs.iterator(chunk_size=batch_size):
            scanned += 1
            update_fields = []

            new_content, content_saved = compact_text(signal.content, content_max)
            if content_saved:
                signal.content = new_content
                update_fields.append("content")
                saved_chars += content_saved

            new_assessment, assessment_saved = compact_text(
                signal.assessment_source_text,
                assessment_max,
            )
            if assessment_saved:
                signal.assessment_source_text = new_assessment
                update_fields.append("assessment_source_text")
                saved_chars += assessment_saved

            if update_fields:
                compacted += 1
                if not dry_run:
                    pending_updates.append(signal)

            if len(pending_updates) >= batch_size:
                Signal.objects.bulk_update(
                    pending_updates,
                    ["content", "assessment_source_text"],
                    batch_size=batch_size,
                )
                pending_updates = []

        if pending_updates and not dry_run:
            Signal.objects.bulk_update(
                pending_updates,
                ["content", "assessment_source_text"],
                batch_size=batch_size,
            )

        deleted_noise = 0
        if options["delete_noise"]:
            noise_qs = Signal.objects.filter(
                status="noise",
                approved_for_mapping=False,
                created_at__lt=noise_cutoff,
            )
            deleted_noise = noise_qs.count()
            if not dry_run:
                noise_qs.delete()

        deleted_raw = 0
        if options["delete_raw"]:
            raw_qs = Signal.objects.filter(
                status="raw",
                approved_for_mapping=False,
                created_at__lt=raw_cutoff,
            )
            deleted_raw = raw_qs.count()
            if not dry_run:
                raw_qs.delete()

        vacuum_done = False
        if options["vacuum"] and not dry_run and connection.vendor == "sqlite":
            with connection.cursor() as cursor:
                cursor.execute("VACUUM")
            vacuum_done = True

        self.stdout.write(
            self.style.SUCCESS(
                "Storage compact selesai. "
                f"Scanned: {scanned}. Compacted: {compacted}. "
                f"Approx saved chars: {saved_chars}. "
                f"Deleted noise: {deleted_noise}. Deleted raw: {deleted_raw}. "
                f"Vacuum: {'yes' if vacuum_done else 'no'}."
            )
        )

        if dry_run:
            self.stdout.write("Dry-run aktif: tidak ada perubahan database.")

