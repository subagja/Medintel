"""Pulihkan CollectionJob dengan lease worker kedaluwarsa."""
from django.core.management.base import BaseCommand
from apps.collection.services.queue import recover_stale_collection_jobs


class Command(BaseCommand):
    help = (
        "Pulihkan job RUNNING yang lease/heartbeat worker-nya kedaluwarsa."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Dipertahankan untuk kompatibilitas; tidak mengubah data.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("DRY RUN: tidak ada perubahan."))
            return
        result = recover_stale_collection_jobs()
        self.stdout.write(
            self.style.SUCCESS(
                "Pemulihan selesai: "
                f"retry={result['recovered']}, gagal={result['failed']}, "
                f"dijeda={result['paused']}, dibatalkan={result['cancelled']}."
            )
        )
