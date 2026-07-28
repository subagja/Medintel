from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from apps.indicators.models import Indicator
from apps.signals.services import (
    generate_signal_from_indicator,
)


class Command(BaseCommand):
    help = (
        "Membentuk sinyal Intelijen Medik dari "
        "indikator tervalidasi."
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
        total_signals_created = 0
        total_indicators_added = 0
        total_articles_added = 0
        total_requirements_added = 0
        total_skipped = 0

        for indicator in indicators.iterator():
            result = generate_signal_from_indicator(
                indicator
            )

            total_indicators += 1

            if result.created:
                total_signals_created += 1

            if result.indicator_added:
                total_indicators_added += 1

            total_articles_added += (
                result.articles_added
            )

            total_requirements_added += (
                result.requirements_added
            )

            if result.skipped_reason:
                total_skipped += 1

            self.stdout.write(
                (
                    f"Indicator={indicator.id} | "
                    f"signal="
                    f"{result.signal.code if result.signal else '-'} | "
                    f"created={result.created} | "
                    f"indicator_added={result.indicator_added} | "
                    f"articles_added={result.articles_added} | "
                    f"requirements_added={result.requirements_added} | "
                    f"skipped={result.skipped_reason or '-'}"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                (
                    "Pembentukan sinyal selesai | "
                    f"indikator={total_indicators} | "
                    f"sinyal baru={total_signals_created} | "
                    f"indikator ditambahkan="
                    f"{total_indicators_added} | "
                    f"artikel ditambahkan="
                    f"{total_articles_added} | "
                    f"kebutuhan ditambahkan="
                    f"{total_requirements_added} | "
                    f"dilewati={total_skipped}"
                )
            )
        )