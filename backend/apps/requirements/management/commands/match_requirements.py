from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from apps.indicators.models import Indicator
from apps.requirements.services import (
    match_indicator_to_requirements,
)


class Command(BaseCommand):
    help = (
        "Mencocokkan indikator tervalidasi dengan "
        "kebutuhan intelijen aktif."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--indicator-id",
            type=str,
            help="UUID indikator tertentu.",
        )

        parser.add_argument(
            "--all",
            action="store_true",
            help=(
                "Proses seluruh indikator tervalidasi "
                "atau dikoreksi."
            ),
        )

    def handle(self, *args, **options):
        indicator_id = options.get(
            "indicator_id"
        )
        process_all = options.get("all")

        if not indicator_id and not process_all:
            raise CommandError(
                "Gunakan --indicator-id <UUID> atau --all."
            )

        if indicator_id and process_all:
            raise CommandError(
                "Gunakan salah satu: "
                "--indicator-id atau --all."
            )

        if indicator_id:
            try:
                indicators = Indicator.objects.filter(
                    id=indicator_id
                )

                if not indicators.exists():
                    raise Indicator.DoesNotExist

            except (
                Indicator.DoesNotExist,
                ValueError,
            ) as exc:
                raise CommandError(
                    f"Indikator tidak ditemukan: {indicator_id}"
                ) from exc
        else:
            indicators = Indicator.objects.filter(
                status__in=[
                    Indicator.Status.VALIDATED,
                    Indicator.Status.CORRECTED,
                ]
            )

        total_indicators = 0
        total_created = 0
        total_updated = 0
        total_skipped = 0

        for indicator in indicators.iterator():
            result = match_indicator_to_requirements(
                indicator
            )

            total_indicators += 1
            total_created += len(
                result.matches_created
            )
            total_updated += len(
                result.matches_updated
            )

            if result.skipped_reason:
                total_skipped += 1

            self.stdout.write(
                (
                    f"Indicator={indicator.id} | "
                    f"created="
                    f"{len(result.matches_created)} | "
                    f"updated="
                    f"{len(result.matches_updated)} | "
                    f"skipped="
                    f"{result.skipped_reason or '-'}"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                (
                    "Pencocokan kebutuhan selesai | "
                    f"indikator={total_indicators} | "
                    f"dibuat={total_created} | "
                    f"diperbarui={total_updated} | "
                    f"dilewati={total_skipped}"
                )
            )
        )