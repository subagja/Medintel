from django.core.management.base import BaseCommand

from apps.crawlers.implementations.static import StaticTestCrawler
from apps.crawlers.services import run_crawler


class Command(BaseCommand):
    help = "Menjalankan crawler statis untuk menguji pipeline ingestion."

    def handle(self, *args, **options):
        crawler = StaticTestCrawler()

        result = run_crawler(crawler)

        self.stdout.write(
            self.style.SUCCESS(
                (
                    "Crawler selesai | "
                    f"ditemukan={result.total_found} | "
                    f"dibuat={result.total_created} | "
                    f"duplikat={result.total_duplicate} | "
                    f"ditolak={result.total_rejected} | "
                    f"gagal={result.total_failed}"
                )
            )
        )