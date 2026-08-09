from __future__ import annotations

from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.crawlers.http_client import CrawlerHttpClient
from apps.sources.models import Source, SourceSeedUrl
from apps.sources.rss_audit import (
    FeedAuditResult,
    audit_feed_candidate,
    collect_feed_candidates,
    source_activation_blockers,
)
from apps.sources.rss_registry import KNOWN_WITHOUT_SAFE_OFFICIAL_RSS


@dataclass
class AuditSummary:
    inspected: int = 0
    blocked: int = 0
    no_candidate: int = 0
    valid_sources: int = 0
    valid_feeds: int = 0
    invalid_feeds: int = 0
    activated_sources: int = 0
    activated_feeds: int = 0


class Command(BaseCommand):
    help = (
        "Audit endpoint RSS/Atom resmi dan aktifkan hanya feed yang valid, "
        "berisi entri, serta URL artikelnya lolos policy Source."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            action="append",
            dest="source_codes",
            default=[],
            help=(
                "Kode Source yang diaudit. Dapat diulang. Jika kosong, "
                "seluruh Source Master diperiksa."
            ),
        )
        parser.add_argument(
            "--feed-url",
            action="append",
            dest="feed_urls",
            default=[],
            help=(
                "Kandidat URL tambahan. Hanya boleh dipakai bila tepat satu "
                "--source diberikan."
            ),
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Simpan seed RSS valid dan aktifkan crawl_enabled. Tanpa "
                "opsi ini command hanya melakukan dry-run."
            ),
        )
        parser.add_argument(
            "--no-html-discovery",
            action="store_true",
            help="Jangan membaca deklarasi RSS/Atom pada HTML Source.",
        )
        parser.add_argument(
            "--sample-size",
            type=int,
            default=12,
            help="Jumlah maksimum URL entri yang diuji per feed (default 12).",
        )
        parser.add_argument(
            "--minimum-ratio",
            type=float,
            default=0.5,
            help=(
                "Proporsi minimum URL contoh yang harus lolos policy Source "
                "(default 0.5)."
            ),
        )
        parser.add_argument(
            "--max-feeds-per-source",
            type=int,
            default=3,
            help="Jumlah maksimum feed valid yang diaktifkan per Source.",
        )

    def handle(self, *args, **options):
        source_codes = tuple(dict.fromkeys(options["source_codes"]))
        feed_urls = tuple(dict.fromkeys(options["feed_urls"]))
        should_apply = options["apply"]
        sample_size = options["sample_size"]
        minimum_ratio = options["minimum_ratio"]
        max_feeds = options["max_feeds_per_source"]

        if feed_urls and len(source_codes) != 1:
            raise CommandError(
                "--feed-url memerlukan tepat satu argumen --source."
            )
        if sample_size < 1:
            raise CommandError("--sample-size minimal bernilai 1.")
        if not 0 < minimum_ratio <= 1:
            raise CommandError("--minimum-ratio harus lebih dari 0 dan <= 1.")
        if max_feeds < 1:
            raise CommandError("--max-feeds-per-source minimal bernilai 1.")

        queryset = Source.objects.prefetch_related(
            "seed_urls",
            "url_patterns",
        ).order_by("source_type", "name")

        if source_codes:
            queryset = queryset.filter(code__in=source_codes)

        sources = list(queryset)

        if source_codes:
            existing_codes = {source.code for source in sources}
            missing_codes = sorted(set(source_codes) - existing_codes)

            if missing_codes:
                raise CommandError(
                    "Source tidak ditemukan: " + ", ".join(missing_codes)
                )

        if not sources:
            raise CommandError("Belum ada Source yang dapat diaudit.")

        self.stdout.write(
            self.style.MIGRATE_HEADING("Audit dan aktivasi RSS resmi")
        )
        self.stdout.write(
            f"Mode                  : {'APPLY' if should_apply else 'DRY-RUN'}"
        )
        self.stdout.write(f"Source diperiksa      : {len(sources)}")
        self.stdout.write(
            "Policy                : feed XML valid + entri artikel + "
            f"rasio lolos >= {minimum_ratio:.0%}"
        )

        summary = AuditSummary()

        for source in sources:
            summary.inspected += 1
            self.stdout.write("")
            self.stdout.write(
                self.style.HTTP_INFO(f"[{source.code}] {source.name}")
            )

            blockers = source_activation_blockers(source)

            if blockers:
                summary.blocked += 1
                self.stdout.write(
                    self.style.WARNING("  BLOCKED: " + "; ".join(blockers))
                )
                continue

            manual_urls = feed_urls if len(sources) == 1 else ()
            valid_results: list[FeedAuditResult] = []

            with CrawlerHttpClient(source) as client:
                candidates = collect_feed_candidates(
                    source,
                    client,
                    manual_urls=manual_urls,
                    discover_from_html=not options["no_html_discovery"],
                )

                if candidates.discovery_error:
                    self.stdout.write(
                        "  HTML discovery dilewati: "
                        + candidates.discovery_error
                    )

                if not candidates.urls:
                    summary.no_candidate += 1
                    known_reason = KNOWN_WITHOUT_SAFE_OFFICIAL_RSS.get(
                        source.code,
                        "Tidak ditemukan deklarasi atau kandidat feed resmi.",
                    )
                    self.stdout.write(self.style.WARNING(f"  NO RSS: {known_reason}"))
                    continue

                for feed_url in candidates.urls:
                    result = audit_feed_candidate(
                        source,
                        client,
                        feed_url,
                        sample_size=sample_size,
                        minimum_acceptance_ratio=minimum_ratio,
                    )

                    if result.is_valid:
                        summary.valid_feeds += 1
                        valid_results.append(result)
                        self.stdout.write(
                            self.style.SUCCESS(
                                "  VALID  "
                                f"{result.requested_url} "
                                f"({result.accepted_entries}/"
                                f"{result.checked_entries} URL lolos)"
                            )
                        )
                    else:
                        summary.invalid_feeds += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"  REJECT {result.requested_url} - "
                                f"{result.reason}"
                            )
                        )

            if not valid_results:
                continue

            summary.valid_sources += 1
            selected_results = valid_results[:max_feeds]

            if not should_apply:
                self.stdout.write(
                    self.style.WARNING(
                        f"  DRY-RUN: {len(selected_results)} feed siap diaktifkan."
                    )
                )
                continue

            feed_changes, source_changed = self._activate(
                source,
                selected_results,
            )
            summary.activated_feeds += feed_changes

            if source_changed or feed_changes:
                summary.activated_sources += 1

            self.stdout.write(
                self.style.SUCCESS(
                    "  ACTIVE: "
                    f"{len(selected_results)} feed RSS siap; "
                    f"{feed_changes} seed berubah."
                )
            )

        self._print_summary(summary, should_apply=should_apply)

    @staticmethod
    def _activate(
        source: Source,
        results: list[FeedAuditResult],
    ) -> tuple[int, bool]:
        changed_feeds = 0
        source_changed = False

        with transaction.atomic():
            for index, result in enumerate(results, start=1):
                seed, created = SourceSeedUrl.objects.get_or_create(
                    source=source,
                    url=result.requested_url,
                    defaults={
                        "seed_type": SourceSeedUrl.SeedType.RSS,
                        "priority": index * 10,
                        "is_active": True,
                        "notes": (
                            "RSS resmi; divalidasi otomatis terhadap feed "
                            "dan policy URL Source."
                        ),
                    },
                )

                changed = created

                if not created:
                    if seed.seed_type != SourceSeedUrl.SeedType.RSS:
                        seed.seed_type = SourceSeedUrl.SeedType.RSS
                        changed = True
                    if not seed.is_active:
                        seed.is_active = True
                        changed = True

                    if changed:
                        seed.save(
                            update_fields=[
                                "seed_type",
                                "is_active",
                                "updated_at",
                            ]
                        )

                if changed:
                    changed_feeds += 1

            note = (
                "Kanal RSS resmi diaktifkan setelah feed dan contoh URL "
                "artikel lolos audit otomatis."
            )

            if not source.crawl_enabled:
                source.crawl_enabled = True
                source_changed = True

            if note not in source.crawler_notes:
                source.crawler_notes = (
                    f"{source.crawler_notes}\n{note}"
                ).strip()
                source_changed = True

            if source_changed:
                source.save(
                    update_fields=[
                        "crawl_enabled",
                        "crawler_notes",
                        "updated_at",
                    ]
                )

        return changed_feeds, source_changed

    def _print_summary(
        self,
        summary: AuditSummary,
        *,
        should_apply: bool,
    ) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Ringkasan audit RSS"))
        self.stdout.write(f"Source diperiksa      : {summary.inspected}")
        self.stdout.write(f"Source diblokir       : {summary.blocked}")
        self.stdout.write(f"Tanpa kandidat RSS    : {summary.no_candidate}")
        self.stdout.write(f"Source RSS valid      : {summary.valid_sources}")
        self.stdout.write(f"Feed valid            : {summary.valid_feeds}")
        self.stdout.write(f"Feed ditolak          : {summary.invalid_feeds}")

        if should_apply:
            self.stdout.write(
                f"Source diaktifkan     : {summary.activated_sources}"
            )
            self.stdout.write(
                f"Seed RSS berubah      : {summary.activated_feeds}"
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "Belum ada perubahan database. Jalankan ulang dengan "
                    "--apply setelah memeriksa hasil dry-run."
                )
            )
