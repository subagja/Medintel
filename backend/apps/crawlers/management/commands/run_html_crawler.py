from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from apps.crawlers.real_crawler import (
    GenericHtmlCrawler,
)
from apps.crawlers.services import (
    run_crawler,
)


class Command(BaseCommand):
    help = (
        "Menjalankan crawler HTML nyata "
        "berdasarkan konfigurasi Source."
    )

    def add_arguments(
        self,
        parser,
    ):
        parser.add_argument(
            "--source",
            required=True,
            help=(
                "Kode sumber, misalnya "
                "kemkes atau media-uji."
            ),
        )

        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help=(
                "Batas jumlah artikel "
                "untuk proses ini."
            ),
        )

    def handle(
        self,
        *args,
        **options,
    ):
        source_code = options["source"]
        limit = options["limit"]

        if limit is not None and limit < 1:
            raise CommandError(
                "--limit minimal bernilai 1."
            )

        crawler = GenericHtmlCrawler(
            source_code=source_code,
            limit=limit,
        )

        self.stdout.write(
            (
                "Menjalankan crawler HTML "
                f"untuk source: {source_code}"
            )
        )

        try:
            result = run_crawler(
                crawler,
                trigger_type="manual_command",
            )
        except ValueError as exc:
            raise CommandError(
                str(exc)
            ) from exc

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Crawler selesai."
            )
        )

        self.stdout.write(
            f"Artikel ditemukan : {result.total_found}"
        )

        self.stdout.write(
            f"Artikel baru      : {result.total_created}"
        )

        self.stdout.write(
            f"Artikel duplikat  : {result.total_duplicate}"
        )

        self.stdout.write(
            f"Artikel ditolak   : {result.total_rejected}"
        )

        self.stdout.write(
            f"Artikel gagal     : {result.total_failed}"
        )