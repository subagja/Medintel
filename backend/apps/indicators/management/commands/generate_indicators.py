from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from apps.entities.models import (
    ArticleFact,
    ValidationStatus,
)
from apps.indicators.services import (
    generate_indicators_from_fact,
)


class Command(BaseCommand):
    help = (
        "Membentuk indikator dari fakta artikel yang telah "
        "divalidasi atau dikoreksi."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fact-id",
            type=str,
            help="UUID ArticleFact tertentu.",
        )

        parser.add_argument(
            "--all",
            action="store_true",
            help=(
                "Proses seluruh fakta yang telah divalidasi "
                "atau dikoreksi."
            ),
        )

    def handle(self, *args, **options):
        fact_id = options.get("fact_id")
        process_all = options.get("all")

        if not fact_id and not process_all:
            raise CommandError(
                "Gunakan --fact-id <UUID> atau --all."
            )

        if fact_id and process_all:
            raise CommandError(
                "Gunakan salah satu: --fact-id atau --all."
            )

        if fact_id:
            try:
                facts = ArticleFact.objects.filter(
                    id=fact_id,
                )

                if not facts.exists():
                    raise ArticleFact.DoesNotExist
            except (
                ArticleFact.DoesNotExist,
                ValueError,
            ) as exc:
                raise CommandError(
                    f"Fakta tidak ditemukan: {fact_id}"
                ) from exc
        else:
            facts = ArticleFact.objects.filter(
                validation_status__in=[
                    ValidationStatus.VALIDATED,
                    ValidationStatus.CORRECTED,
                ]
            )

        total_facts = 0
        total_created = 0
        total_existing = 0
        total_skipped = 0

        for fact in facts.iterator():
            result = generate_indicators_from_fact(
                fact
            )

            total_facts += 1
            total_created += len(
                result.indicators_created
            )
            total_existing += len(
                result.indicators_existing
            )

            if result.skipped_reason:
                total_skipped += 1

            self.stdout.write(
                (
                    f"Fact={fact.id} | "
                    f"created="
                    f"{len(result.indicators_created)} | "
                    f"existing="
                    f"{len(result.indicators_existing)} | "
                    f"skipped="
                    f"{result.skipped_reason or '-'}"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                (
                    "Pembentukan indikator selesai | "
                    f"fakta={total_facts} | "
                    f"dibuat={total_created} | "
                    f"sudah ada={total_existing} | "
                    f"dilewati={total_skipped}"
                )
            )
        )