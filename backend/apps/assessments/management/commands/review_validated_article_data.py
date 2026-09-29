"""Lengkapi bukti artikel Valid lama tanpa mengubah keputusan validasi."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from apps.articles.models import Article
from apps.assessments.services.article_data_review import reextract_article_data
from apps.assessments.services.ai_evidence import apply_ai_evidence
from apps.assessments.services.information_balance import (
    has_complete_structured_evidence,
    recommend_information_balance,
)
from apps.entities.models import ArticleFact


class Command(BaseCommand):
    help = "Periksa ulang bukti artikel Valid yang belum lengkap."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--article-id", default=None)
        parser.add_argument(
            "--include-pending", action="store_true",
            help="Ikut periksa artikel Pending tanpa mengubah statusnya.",
        )
        parser.add_argument(
            "--with-ai", action="store_true",
            help="Coba lengkapi bukti yang belum ditemukan oleh aturan dengan AI.",
        )

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("--limit minimal bernilai 1.")

        articles = Article.objects.filter(
            processing_status=Article.ProcessingStatus.VALIDATED,
            validation_assessment__validation_status="validated",
        )
        if options["include_pending"]:
            articles = Article.objects.filter(
                Q(processing_status=Article.ProcessingStatus.VALIDATED,
                  validation_assessment__validation_status="validated")
                | Q(processing_status=Article.ProcessingStatus.PROCESSED,
                    validation_assessment__validation_status="pending")
            )
        articles = articles.select_related("source").order_by("crawled_at")
        if options["article_id"]:
            articles = articles.filter(pk=options["article_id"])

        examined = completed = created_facts = 0
        for article in articles.iterator():
            if has_complete_structured_evidence(article):
                continue
            if examined >= options["limit"]:
                break
            examined += 1
            try:
                with transaction.atomic():
                    before = ArticleFact.objects.filter(article=article).count()
                    complete = reextract_article_data(article)
                    if not complete and options["with_ai"]:
                        # Pakai panggilan AI yang sama, namun abaikan rekomendasi
                        # statusnya: keputusan Valid lama tidak boleh berubah.
                        from .bulk_validate_articles import Command as BulkCommand

                        ai_result = BulkCommand()._call_ai_assessment(
                            article, recommend_information_balance(article),
                        )
                        apply_ai_evidence(article, ai_result.get("evidence"))
                        complete = has_complete_structured_evidence(article)
                    new_facts = (
                        ArticleFact.objects.filter(article=article).count()
                        - before
                    )
                    if options["dry_run"]:
                        transaction.set_rollback(True)
                completed += int(complete)
                created_facts += new_facts
                self.stdout.write(
                    f"{article.pk}: {'lengkap' if complete else 'perlu review'}"
                    f"; fakta baru={new_facts}"
                )
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"{article.pk}: gagal: {exc}")

        self.stdout.write(
            f"Ditinjau={examined}, menjadi lengkap={completed}, "
            f"fakta baru={created_facts}"
            + (" (simulasi, tidak disimpan)" if options["dry_run"] else "")
        )
