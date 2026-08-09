from django.core.management.base import BaseCommand, CommandError

from apps.crawlers.google_news_crawler import (
    GoogleNewsRssCrawler,
    get_google_news_allowed_sources,
    get_google_news_max_age_days,
)
from apps.crawlers.google_news_queries import build_google_news_disease_scope
from apps.crawlers.services import run_crawler


class Command(BaseCommand):
    help = (
        "Menjalankan satu discovery Google News RSS global. Istilah berasal "
        "dari Disease Master dan setiap URL penerbit dipetakan ke Source "
        "aktif/terverifikasi yang lolos allow/deny."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--candidate-limit", type=int, default=None)
        parser.add_argument("--list-allowed", action="store_true")

    def handle(self, *args, **options):
        limit = options["limit"]
        candidate_limit = options["candidate_limit"]
        if limit is not None and limit < 1:
            raise CommandError("--limit minimal bernilai 1.")
        if candidate_limit is not None and candidate_limit < 1:
            raise CommandError("--candidate-limit minimal bernilai 1.")

        allowed_sources = get_google_news_allowed_sources()
        disease_scope = build_google_news_disease_scope()
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Source penerbit diizinkan: {len(allowed_sources)}"
            )
        )
        for source in allowed_sources:
            self.stdout.write(f"- {source.code}: {source.name}")

        if options["list_allowed"]:
            return
        if not allowed_sources:
            raise CommandError(
                "Belum ada Source aktif/terverifikasi dengan crawling dan "
                "pola URL allow."
            )
        if not disease_scope.batches:
            raise CommandError(
                "Disease Master belum memiliki penyakit surveilans aktif."
            )

        self.stdout.write("")
        self.stdout.write(
            f"Cakupan penyakit : {disease_scope.disease_count}"
        )
        self.stdout.write(f"Batch kueri       : {len(disease_scope.batches)}")
        self.stdout.write(
            f"Usia maksimum     : {get_google_news_max_age_days()} hari"
        )

        result = run_crawler(
            GoogleNewsRssCrawler(
                limit=limit,
                candidate_limit=candidate_limit,
            ),
            trigger_type="manual_command",
        )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Ringkasan Google News RSS"))
        self.stdout.write(f"Artikel ditemukan : {result.total_found}")
        self.stdout.write(f"Artikel baru      : {result.total_created}")
        self.stdout.write(f"Artikel duplikat  : {result.total_duplicate}")
        self.stdout.write(f"Artikel ditolak   : {result.total_rejected}")
        self.stdout.write(f"Artikel gagal     : {result.total_failed}")
