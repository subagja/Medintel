from __future__ import annotations

from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError

from apps.crawlers.rss_crawler import OfficialRssCrawler
from apps.crawlers.services import run_crawler
from apps.sources.models import Source, SourceSeedUrl
from apps.sources.services import check_source_seed_readiness


@dataclass
class RssSummary:
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
        "Menjalankan discovery artikel melalui RSS/Atom resmi untuk satu "
        "source atau seluruh source RSS yang siap."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            default=None,
            help=(
                "Kode satu source. Jika tidak diisi, seluruh source yang "
                "memiliki seed RSS aktif dan lolos readiness dijalankan."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help=(
                "Batas artikel per source. Default mengikuti "
                "max_articles_per_run milik source."
            ),
        )
        parser.add_argument(
            "--candidate-limit",
            type=int,
            default=None,
            help="Batas entri RSS yang diunduh sebagai artikel per source.",
        )
        parser.add_argument(
            "--list-ready",
            action="store_true",
            help="Hanya tampilkan source RSS yang siap.",
        )
        parser.add_argument(
            "--show-not-ready",
            action="store_true",
            help="Tampilkan source yang belum siap beserta alasannya.",
        )
        parser.add_argument(
            "--stop-on-error",
            action="store_true",
            help="Hentikan proses bila satu source gagal.",
        )

    def handle(self, *args, **options):
        source_code = options["source"]
        limit = options["limit"]
        candidate_limit = options["candidate_limit"]

        if limit is not None and limit < 1:
            raise CommandError("--limit minimal bernilai 1.")

        if candidate_limit is not None and candidate_limit < 1:
            raise CommandError("--candidate-limit minimal bernilai 1.")

        queryset = Source.objects.prefetch_related(
            "seed_urls",
            "url_patterns",
        ).order_by("source_type", "name")

        if source_code:
            queryset = queryset.filter(code=source_code)

        candidates = list(queryset)

        if not candidates:
            if source_code:
                raise CommandError(
                    f"Source dengan kode '{source_code}' tidak ditemukan."
                )
            raise CommandError("Belum ada source di database.")

        ready_sources: list[Source] = []
        not_ready_sources: list[tuple[Source, tuple[str, ...]]] = []

        for source in candidates:
            readiness = check_source_seed_readiness(
                source,
                seed_types=(SourceSeedUrl.SeedType.RSS,),
            )

            if readiness.is_ready:
                ready_sources.append(source)
            else:
                not_ready_sources.append((source, readiness.errors))

        if options["show_not_ready"]:
            self._print_not_ready(not_ready_sources)

        if options["list_ready"]:
            self._print_ready(ready_sources)
            return

        if source_code and not ready_sources:
            source, errors = not_ready_sources[0]
            raise CommandError(
                f"Source '{source.code}' belum siap untuk RSS resmi: "
                + "; ".join(errors)
            )

        if not ready_sources:
            raise CommandError(
                "Tidak ada source RSS resmi yang siap. Tambahkan seed "
                "berjenis RSS/Atom Feed pada Sumber OSINT."
            )

        self._print_ready(ready_sources)
        summary = RssSummary(source_total=len(ready_sources))

        for index, source in enumerate(ready_sources, start=1):
            self.stdout.write("")
            self.stdout.write(
                self.style.HTTP_INFO(
                    f"[{index}/{summary.source_total}] "
                    f"{source.name} ({source.code})"
                )
            )
            crawler = OfficialRssCrawler(
                source_code=source.code,
                limit=(
                    limit
                    if limit is not None
                    else source.max_articles_per_run
                ),
                candidate_limit=candidate_limit,
            )

            try:
                result = run_crawler(
                    crawler,
                    trigger_type="manual_command",
                )
            except Exception as exc:
                summary.source_failed += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"Source RSS gagal: {source.code} - {exc}"
                    )
                )

                if options["stop_on_error"]:
                    raise CommandError(
                        "RSS dihentikan karena --stop-on-error aktif."
                    ) from exc
                continue

            summary.source_success += 1
            summary.article_found += result.total_found
            summary.article_created += result.total_created
            summary.article_duplicate += result.total_duplicate
            summary.article_rejected += result.total_rejected
            summary.article_failed += result.total_failed
            self.stdout.write(
                self.style.SUCCESS(f"Source selesai: {source.code}")
            )
            self.stdout.write(f"  Ditemukan : {result.total_found}")
            self.stdout.write(f"  Baru      : {result.total_created}")
            self.stdout.write(f"  Duplikat  : {result.total_duplicate}")
            self.stdout.write(f"  Ditolak   : {result.total_rejected}")
            self.stdout.write(f"  Gagal     : {result.total_failed}")

        self._print_summary(summary)

    def _print_ready(self, sources: list[Source]) -> None:
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Source RSS resmi siap: {len(sources)}"
            )
        )

        for source in sources:
            self.stdout.write(f"- {source.code}: {source.name}")

    def _print_not_ready(
        self,
        sources: list[tuple[Source, tuple[str, ...]]],
    ) -> None:
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(f"Source RSS belum siap: {len(sources)}")
        )

        for source, errors in sources:
            self.stdout.write(f"- {source.code}: {'; '.join(errors)}")

    def _print_summary(self, summary: RssSummary) -> None:
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING("Ringkasan crawling RSS resmi")
        )
        self.stdout.write(f"Source diproses   : {summary.source_total}")
        self.stdout.write(f"Source berhasil   : {summary.source_success}")
        self.stdout.write(f"Source gagal      : {summary.source_failed}")
        self.stdout.write(f"Artikel ditemukan : {summary.article_found}")
        self.stdout.write(f"Artikel baru      : {summary.article_created}")
        self.stdout.write(f"Artikel duplikat  : {summary.article_duplicate}")
        self.stdout.write(f"Artikel ditolak   : {summary.article_rejected}")
        self.stdout.write(f"Artikel gagal     : {summary.article_failed}")
