# backend/intel/management/commands/triage_signals.py

from django.core.management.base import BaseCommand
from intel.models import Signal
from intel.services.triage import apply_triage


class Command(BaseCommand):
    help = "Apply triage classification to raw and validated signals"

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Apply triage to all signals, including verified and noise",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit number of processed signals",
        )

    def handle(self, *args, **options):
        qs = Signal.objects.all()

        if not options["all"]:
            qs = qs.filter(status__in=["raw", "validated"])

        qs = qs.order_by("-published_at", "-id")

        if options["limit"]:
            qs = qs[: options["limit"]]

        total = qs.count() if hasattr(qs, "count") else len(qs)
        updated = 0

        for signal in qs.iterator() if hasattr(qs, "iterator") else qs:
            apply_triage(signal)
            updated += 1

            if updated % 100 == 0:
                self.stdout.write(f"Processed {updated}/{total}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Triage completed: {updated}/{total} signals updated"
            )
        )