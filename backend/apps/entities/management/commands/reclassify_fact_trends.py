from django.core.management.base import BaseCommand

from apps.entities.models import (
    ArticleFact,
    ExtractionMethod,
    ValidationStatus,
)
from apps.entities.services.fact_extraction import detect_trend


class Command(BaseCommand):
    help = (
        "Audit dan klasifikasikan ulang tren fakta otomatis dengan "
        "konteks epidemiologis yang lebih ketat."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Simpan perubahan. Tanpa opsi ini hanya dry-run.",
        )
        parser.add_argument(
            "--article",
            help="Batasi pada UUID satu artikel.",
        )
        parser.add_argument(
            "--include-reviewed",
            action="store_true",
            help=(
                "Sertakan fakta tervalidasi/dikoreksi. Gunakan hanya "
                "setelah peninjauan karena dapat memengaruhi analisis."
            ),
        )

    def handle(self, *args, **options):
        queryset = (
            ArticleFact.objects.exclude(
                extraction_method=ExtractionMethod.MANUAL,
            )
            .select_related("article")
            .order_by("created_at")
        )

        if not options["include_reviewed"]:
            queryset = queryset.filter(
                validation_status=ValidationStatus.UNREVIEWED,
            )

        if options.get("article"):
            queryset = queryset.filter(
                article_id=options["article"],
            )

        inspected = 0
        changed = 0
        applied = 0

        for fact in queryset.iterator():
            inspected += 1
            article_text = "\n".join(
                part
                for part in (
                    fact.article.title,
                    fact.article.content_text,
                )
                if part
            )
            mention = detect_trend(article_text)
            new_trend = (
                mention.trend
                if mention is not None
                else ArticleFact.Trend.UNKNOWN
            )

            if new_trend == fact.trend:
                continue

            changed += 1
            self.stdout.write(
                f"{fact.id} | {fact.get_trend_display()} -> "
                f"{ArticleFact.Trend(new_trend).label} | "
                f"{fact.article.title[:90]}"
            )

            if options["apply"]:
                fact.trend = new_trend
                fact.save(update_fields=["trend", "updated_at"])
                applied += 1

        mode = "APPLY" if options["apply"] else "DRY-RUN"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} selesai: diperiksa={inspected}, "
                f"berubah={changed}, disimpan={applied}."
            )
        )
