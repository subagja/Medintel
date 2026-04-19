from django.core.management.base import BaseCommand
from django.db import transaction

from intel.models import Signal, SignalLocation
from intel.utils.location_resolver import resolve_location_from_text


class Command(BaseCommand):
    help = "Backfill SignalLocation from Signal.raw_location_text"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--only-empty", action="store_true")
        parser.add_argument("--update-existing", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        qs = Signal.objects.all().order_by("id")

        if options["only_empty"]:
            qs = qs.exclude(
                id__in=SignalLocation.objects.values_list("signal_id", flat=True)
            )

        limit = options["limit"]
        if limit and limit > 0:
            qs = qs[:limit]

        update_existing = options["update_existing"]

        total = 0
        matched = 0
        ambiguous = 0
        not_found = 0
        created = 0
        updated = 0
        skipped = 0

        for signal in qs:
            raw_text = signal.raw_location_text or ""
            result = resolve_location_from_text(raw_text)
            total += 1

            if result.method == "ambiguous":
                ambiguous += 1
            elif result.method == "not_found":
                not_found += 1

            if not result.location_obj:
                self.stdout.write(
                    self.style.WARNING(
                        f"[NO MATCH] Signal #{signal.id} | raw_location_text={raw_text!r} | method={result.method}"
                    )
                )
                continue

            if result.method not in {"ambiguous", "not_found", "empty"}:
                matched += 1

            existing = SignalLocation.objects.filter(signal=signal, is_primary=True).first()

            if existing:
                if update_existing:
                    existing.location = result.location_obj
                    existing.raw_location_text = raw_text
                    existing.confidence = result.confidence
                    existing.method = result.method
                    existing.is_primary = True
                    existing.save()

                    updated += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[UPDATED] Signal #{signal.id} -> {result.location_obj.display_name} ({result.method})"
                        )
                    )
                else:
                    skipped += 1
                    self.stdout.write(
                        f"[EXISTS] Signal #{signal.id} already has primary SignalLocation"
                    )
                continue

            SignalLocation.objects.create(
                signal=signal,
                location=result.location_obj,
                raw_location_text=raw_text,
                confidence=result.confidence,
                method=result.method,
                is_primary=True,
            )
            created += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"[CREATED] Signal #{signal.id} -> {result.location_obj.display_name} ({result.method})"
                )
            )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== BACKFILL SUMMARY ==="))
        self.stdout.write(f"Total checked : {total}")
        self.stdout.write(f"Matched       : {matched}")
        self.stdout.write(f"Ambiguous     : {ambiguous}")
        self.stdout.write(f"Not found     : {not_found}")
        self.stdout.write(f"Created       : {created}")
        self.stdout.write(f"Updated       : {updated}")
        self.stdout.write(f"Skipped       : {skipped}")