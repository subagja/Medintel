from uuid import UUID

from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from apps.articles.models import Article
from apps.entities.services import extract_article_entities


class Command(BaseCommand):
    help = (
        "Menjalankan ekstraksi penyakit dan lokasi berbasis aturan "
        "terhadap artikel."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--article-id",
            type=UUID,
            help="UUID artikel tertentu.",
        )

        parser.add_argument(
            "--all",
            action="store_true",
            help="Proses seluruh artikel yang tersedia.",
        )

    def handle(self, *args, **options):
        article_id = options.get("article_id")
        process_all = options.get("all")

        if not article_id and not process_all:
            raise CommandError(
                "Gunakan --article-id <UUID> atau --all."
            )

        if article_id and process_all:
            raise CommandError(
                "Gunakan salah satu: --article-id atau --all."
            )

        if article_id:
            try:
                article = Article.objects.get(
                    id=article_id,
                )
            except Article.DoesNotExist as exc:
                raise CommandError(
                    f"Artikel tidak ditemukan: {article_id}"
                ) from exc

            articles = Article.objects.filter(
                id=article.id,
            )

        else:
            articles = Article.objects.exclude(
                processing_status=Article.ProcessingStatus.REJECTED,
            )

        total_articles = 0
        total_diseases = 0
        total_locations = 0

        for article in articles.iterator():
            extraction = extract_article_entities(
                article
            )

            total_articles += 1
            total_diseases += (
                extraction.diseases_created
                + extraction.diseases_updated
            )
            total_locations += (
                extraction.locations_created
                + extraction.locations_updated
            )

            self.stdout.write(
                (
                    f"Artikel: {article.title} | "
                    f"penyakit={len(extraction.disease_mentions)} | "
                    f"lokasi={len(extraction.location_mentions)}"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                (
                    "Ekstraksi selesai | "
                    f"artikel={total_articles} | "
                    f"relasi penyakit={total_diseases} | "
                    f"relasi lokasi={total_locations}"
                )
            )
        )
