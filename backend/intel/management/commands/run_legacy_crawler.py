from django.core.management.base import BaseCommand
from intel.services.legacy_crawler_adapter import run_legacy_crawler_ingest


class Command(BaseCommand):
    help = "Run legacy crawler and ingest results into Signal / SignalLocation"

    def handle(self, *args, **options):
        result = run_legacy_crawler_ingest()

        self.stdout.write(self.style.SUCCESS("=== LEGACY CRAWLER INGEST SUMMARY ==="))
        self.stdout.write(f"Total rows         : {result['total_rows']}")
        self.stdout.write(f"Created signals    : {result['created']}")
        self.stdout.write(f"Updated signals    : {result['updated']}")
        self.stdout.write(f"Matched locations  : {result['matched_locations']}")