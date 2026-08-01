from __future__ import annotations

from dataclasses import dataclass

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
from apps.sources.models import Source
from apps.sources.services import (
    check_source_crawl_readiness,
)


@dataclass
class CrawlSummary:
    source_total: int = 0
    source_success: int = 0
    source_failed: int = 0
    article_found: int = 0
    article_created: int = 0
    article_duplicate: int = 0
    article_rejected: int = 0
    article_failed: int = 0


class Command(BaseCommand):
    help = (
        "Menjalankan GenericHtmlCrawler untuk satu source "
        "atau seluruh source yang benar-benar siap dicrawl."
    )

    def add_arguments(
        self,
        parser,
    ):
        parser.add_argument(
            "--source",
            default=None,
            help=(
                "Kode satu source. Jika tidak diisi, seluruh "
                "source yang lolos pemeriksaan readiness dijalankan."
            ),
        )

        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help=(
                "Batas artikel per source. Jika tidak diisi, "
                "menggunakan max_articles_per_run milik source."
            ),
        )

        parser.add_argument(
            "--candidate-limit",
            type=int,
            default=None,
            help=(
                "Batas maksimum kandidat listing yang benar-benar "
                "diunduh per source. Default menggunakan setting "
                "CRAWLER_CANDIDATE_LIMIT atau 30."
            ),
        )

        parser.add_argument(
            "--list-ready",
            action="store_true",
            help=(
                "Hanya menampilkan daftar source yang siap "
                "dan tidak menjalankan crawling."
            ),
        )

        parser.add_argument(
            "--show-not-ready",
            action="store_true",
            help=(
                "Tampilkan source yang belum siap beserta "
                "alasan readiness-nya."
            ),
        )

        parser.add_argument(
            "--stop-on-error",
            action="store_true",
            help=(
                "Hentikan proses ketika satu source gagal. "
                "Default: source berikutnya tetap diproses."
            ),
        )

    def handle(
        self,
        *args,
        **options,
    ):
        source_code = options["source"]
        limit = options["limit"]
        candidate_limit = options["candidate_limit"]
        list_ready = options["list_ready"]
        show_not_ready = options["show_not_ready"]
        stop_on_error = options["stop_on_error"]

        if limit is not None and limit < 1:
            raise CommandError(
                "--limit minimal bernilai 1."
            )

        if (
            candidate_limit is not None
            and candidate_limit < 1
        ):
            raise CommandError(
                "--candidate-limit minimal bernilai 1."
            )

        candidates = self._get_candidates(
            source_code=source_code,
        )

        if not candidates:
            if source_code:
                raise CommandError(
                    f"Source dengan kode '{source_code}' "
                    "tidak ditemukan."
                )

            raise CommandError(
                "Belum ada source di database."
            )

        ready_sources: list[Source] = []
        not_ready_sources: list[
            tuple[Source, tuple[str, ...]]
        ] = []

        for source in candidates:
            readiness = check_source_crawl_readiness(
                source
            )

            if (
                readiness.is_ready
                and source.crawl_strategy
                == Source.CrawlStrategy.HTML
            ):
                ready_sources.append(
                    source
                )
                continue

            errors = list(
                readiness.errors
            )

            if (
                source.crawl_strategy
                != Source.CrawlStrategy.HTML
            ):
                errors.append(
                    "Command ini hanya mendukung "
                    "crawl_strategy=html."
                )

            not_ready_sources.append(
                (
                    source,
                    tuple(errors),
                )
            )

        if show_not_ready:
            self._print_not_ready(
                not_ready_sources
            )

        if list_ready:
            self._print_ready(
                ready_sources
            )
            return

        if source_code and not ready_sources:
            source, errors = (
                not_ready_sources[0]
            )

            error_text = "; ".join(
                errors
            )

            raise CommandError(
                (
                    f"Source '{source.code}' belum siap "
                    f"dicrawl: {error_text}"
                )
            )

        if not ready_sources:
            raise CommandError(
                (
                    "Tidak ada source HTML yang siap "
                    "dicrawl. Jalankan dengan "
                    "--show-not-ready untuk melihat alasannya."
                )
            )

        self._print_ready(
            ready_sources
        )

        summary = CrawlSummary(
            source_total=len(
                ready_sources
            )
        )

        for index, source in enumerate(
            ready_sources,
            start=1,
        ):
            self.stdout.write("")

            self.stdout.write(
                self.style.HTTP_INFO(
                    (
                        f"[{index}/"
                        f"{summary.source_total}] "
                        f"{source.name} "
                        f"({source.code})"
                    )
                )
            )

            effective_limit = (
                limit
                if limit is not None
                else source.max_articles_per_run
            )

            crawler = GenericHtmlCrawler(
                source_code=source.code,
                limit=effective_limit,
                candidate_limit=candidate_limit,
            )

            try:
                result = run_crawler(
                    crawler,
                    trigger_type=(
                        "manual_command"
                    ),
                )
            except Exception as exc:
                summary.source_failed += 1

                self.stderr.write(
                    self.style.ERROR(
                        (
                            "Source gagal: "
                            f"{source.code} - {exc}"
                        )
                    )
                )

                if stop_on_error:
                    raise CommandError(
                        (
                            "Crawling dihentikan karena "
                            "--stop-on-error aktif."
                        )
                    ) from exc

                continue

            summary.source_success += 1
            summary.article_found += (
                result.total_found
            )
            summary.article_created += (
                result.total_created
            )
            summary.article_duplicate += (
                result.total_duplicate
            )
            summary.article_rejected += (
                result.total_rejected
            )
            summary.article_failed += (
                result.total_failed
            )

            self.stdout.write(
                self.style.SUCCESS(
                    (
                        "Source selesai: "
                        f"{source.code}"
                    )
                )
            )
            self.stdout.write(
                (
                    "  Ditemukan : "
                    f"{result.total_found}"
                )
            )
            self.stdout.write(
                (
                    "  Baru      : "
                    f"{result.total_created}"
                )
            )
            self.stdout.write(
                (
                    "  Duplikat  : "
                    f"{result.total_duplicate}"
                )
            )
            self.stdout.write(
                (
                    "  Ditolak   : "
                    f"{result.total_rejected}"
                )
            )
            self.stdout.write(
                (
                    "  Gagal     : "
                    f"{result.total_failed}"
                )
            )

        self._print_summary(
            summary
        )

    def _get_candidates(
        self,
        *,
        source_code: str | None,
    ) -> list[Source]:
        queryset = (
            Source.objects
            .prefetch_related(
                "seed_urls",
                "url_patterns",
            )
            .order_by(
                "source_type",
                "name",
            )
        )

        if source_code:
            queryset = queryset.filter(
                code=source_code
            )

        return list(
            queryset
        )

    def _print_ready(
        self,
        sources: list[Source],
    ) -> None:
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                (
                    "Source HTML siap dicrawl: "
                    f"{len(sources)}"
                )
            )
        )

        for source in sources:
            self.stdout.write(
                (
                    f"- {source.code}: "
                    f"{source.name}"
                )
            )

    def _print_not_ready(
        self,
        sources: list[
            tuple[Source, tuple[str, ...]]
        ],
    ) -> None:
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                (
                    "Source belum siap: "
                    f"{len(sources)}"
                )
            )
        )

        for source, errors in sources:
            reason = "; ".join(
                errors
            )

            self.stdout.write(
                (
                    f"- {source.code}: "
                    f"{reason}"
                )
            )

    def _print_summary(
        self,
        summary: CrawlSummary,
    ) -> None:
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Ringkasan seluruh crawling"
            )
        )

        self.stdout.write(
            (
                "Source diproses   : "
                f"{summary.source_total}"
            )
        )
        self.stdout.write(
            (
                "Source berhasil   : "
                f"{summary.source_success}"
            )
        )
        self.stdout.write(
            (
                "Source gagal      : "
                f"{summary.source_failed}"
            )
        )
        self.stdout.write(
            (
                "Artikel ditemukan : "
                f"{summary.article_found}"
            )
        )
        self.stdout.write(
            (
                "Artikel baru      : "
                f"{summary.article_created}"
            )
        )
        self.stdout.write(
            (
                "Artikel duplikat  : "
                f"{summary.article_duplicate}"
            )
        )
        self.stdout.write(
            (
                "Artikel ditolak   : "
                f"{summary.article_rejected}"
            )
        )
        self.stdout.write(
            (
                "Artikel gagal     : "
                f"{summary.article_failed}"
            )
        )

        if summary.source_failed:
            self.stdout.write(
                self.style.WARNING(
                    (
                        "Crawling selesai dengan "
                        "sebagian source gagal."
                    )
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    (
                        "Seluruh source siap-crawl "
                        "selesai diproses."
                    )
                )
            )
