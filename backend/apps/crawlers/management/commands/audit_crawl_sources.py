from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from apps.crawlers.html_parser import (
    discover_article_links,
    parse_article_html,
)
from apps.crawlers.http_client import (
    CrawlerHttpClient,
    CrawlerHttpError,
    RobotsDeniedError,
)
from apps.sources.models import (
    Source,
    SourceSeedUrl,
)
from apps.sources.services import (
    check_source_crawl_readiness,
    normalize_url,
    validate_source_url,
)


@dataclass
class SeedAudit:
    url: str
    seed_type: str
    is_active: bool
    fetch_ok: bool = False
    final_url: str = ""
    discovered_links: int = 0
    valid_article_candidates: int = 0
    sample_article_url: str = ""
    sample_title: str = ""
    sample_content_length: int = 0
    parser_ok: bool = False
    error: str = ""


@dataclass
class SourceAudit:
    code: str
    name: str
    domain: str
    source_type: str
    is_active: bool
    is_verified: bool
    crawl_enabled: bool
    crawl_strategy: str
    readiness_ok: bool
    readiness_errors: list[str]
    active_seed_count: int
    active_allow_pattern_count: int
    active_deny_pattern_count: int
    live_status: str
    recommendation: str
    seeds: list[dict[str, Any]]


class Command(BaseCommand):
    help = (
        "Mengaudit konfigurasi dan akses langsung seluruh source "
        "tanpa menyimpan artikel ke database."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            default=None,
            help="Opsional: audit satu kode source saja.",
        )
        parser.add_argument(
            "--output",
            default="data/exports/crawl_source_audit.json",
            help=(
                "Lokasi file JSON hasil audit. "
                "Default: data/exports/crawl_source_audit.json"
            ),
        )
        parser.add_argument(
            "--max-links",
            type=int,
            default=200,
            help="Maksimum link listing yang diperiksa per seed.",
        )
        parser.add_argument(
            "--max-seeds",
            type=int,
            default=3,
            help="Maksimum seed aktif yang diuji per source.",
        )
        parser.add_argument(
            "--skip-live",
            action="store_true",
            help="Hanya audit konfigurasi database tanpa request internet.",
        )

    def handle(self, *args, **options):
        source_code = options["source"]
        output_path = Path(options["output"])
        max_links = options["max_links"]
        max_seeds = options["max_seeds"]
        skip_live = options["skip_live"]

        if max_links < 1:
            raise CommandError("--max-links minimal 1.")
        if max_seeds < 1:
            raise CommandError("--max-seeds minimal 1.")

        queryset = (
            Source.objects
            .prefetch_related("seed_urls", "url_patterns")
            .order_by("source_type", "name")
        )

        if source_code:
            queryset = queryset.filter(code=source_code)

        sources = list(queryset)

        if not sources:
            raise CommandError("Source tidak ditemukan.")

        report: list[dict[str, Any]] = []

        total = len(sources)

        for index, source in enumerate(sources, start=1):
            self.stdout.write(
                f"[{index}/{total}] Audit {source.code}"
            )

            result = self._audit_source(
                source=source,
                max_links=max_links,
                max_seeds=max_seeds,
                skip_live=skip_live,
            )
            report.append(asdict(result))

        summary = self._build_summary(report)

        payload = {
            "summary": summary,
            "sources": report,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Audit selesai: {output_path}"
            )
        )
        self.stdout.write(
            f"Total source          : {summary['total_sources']}"
        )
        self.stdout.write(
            f"Ready secara config   : {summary['config_ready']}"
        )
        self.stdout.write(
            f"Live test berhasil    : {summary['live_passed']}"
        )
        self.stdout.write(
            f"Perlu update config   : {summary['needs_config']}"
        )
        self.stdout.write(
            f"Perlu review manual   : {summary['manual_review']}"
        )
        self.stdout.write(
            f"Tetap dinonaktifkan   : {summary['keep_disabled']}"
        )

    def _audit_source(
        self,
        *,
        source: Source,
        max_links: int,
        max_seeds: int,
        skip_live: bool,
    ) -> SourceAudit:
        readiness = check_source_crawl_readiness(source)

        active_seeds = list(
            source.seed_urls
            .filter(is_active=True)
            .order_by("priority", "url")[:max_seeds]
        )

        active_allow_count = source.url_patterns.filter(
            is_active=True,
            pattern_type="allow",
        ).count()

        active_deny_count = source.url_patterns.filter(
            is_active=True,
            pattern_type="deny",
        ).count()

        seed_results: list[SeedAudit] = []

        if skip_live:
            live_status = "SKIPPED"
        elif not source.is_active:
            live_status = "NOT_TESTED_INACTIVE"
        elif not source.is_verified:
            live_status = "NOT_TESTED_UNVERIFIED"
        elif not active_seeds:
            live_status = "NO_ACTIVE_SEED"
        elif not active_allow_count:
            live_status = "NO_ALLOW_PATTERN"
        else:
            live_status, seed_results = self._run_live_test(
                source=source,
                seeds=active_seeds,
                max_links=max_links,
            )

        recommendation = self._recommend(
            source=source,
            readiness_ok=readiness.is_ready,
            live_status=live_status,
            active_seed_count=len(active_seeds),
            active_allow_count=active_allow_count,
        )

        return SourceAudit(
            code=source.code,
            name=source.name,
            domain=source.domain,
            source_type=source.source_type,
            is_active=source.is_active,
            is_verified=source.is_verified,
            crawl_enabled=source.crawl_enabled,
            crawl_strategy=source.crawl_strategy,
            readiness_ok=readiness.is_ready,
            readiness_errors=list(readiness.errors),
            active_seed_count=len(active_seeds),
            active_allow_pattern_count=active_allow_count,
            active_deny_pattern_count=active_deny_count,
            live_status=live_status,
            recommendation=recommendation,
            seeds=[asdict(item) for item in seed_results],
        )

    def _run_live_test(
        self,
        *,
        source: Source,
        seeds: list[SourceSeedUrl],
        max_links: int,
    ) -> tuple[str, list[SeedAudit]]:
        results: list[SeedAudit] = []
        any_fetch_ok = False
        any_candidate = False
        any_parser_ok = False

        try:
            with CrawlerHttpClient(source) as client:
                for seed in seeds:
                    result = self._audit_seed(
                        source=source,
                        seed=seed,
                        client=client,
                        max_links=max_links,
                    )
                    results.append(result)

                    any_fetch_ok = (
                        any_fetch_ok or result.fetch_ok
                    )
                    any_candidate = (
                        any_candidate
                        or result.valid_article_candidates > 0
                    )
                    any_parser_ok = (
                        any_parser_ok or result.parser_ok
                    )
        except Exception as exc:
            return (
                f"CLIENT_ERROR: {exc}",
                results,
            )

        if any_parser_ok:
            return "PASS", results
        if any_candidate:
            return "PARSER_FAILED", results
        if any_fetch_ok:
            return "NO_VALID_ARTICLE_LINK", results
        return "SEED_FETCH_FAILED", results

    def _audit_seed(
        self,
        *,
        source: Source,
        seed: SourceSeedUrl,
        client: CrawlerHttpClient,
        max_links: int,
    ) -> SeedAudit:
        result = SeedAudit(
            url=seed.url,
            seed_type=seed.seed_type,
            is_active=seed.is_active,
        )

        try:
            page = client.get_html(seed.url)
        except (CrawlerHttpError, RobotsDeniedError) as exc:
            result.error = str(exc)
            return result
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            return result

        result.fetch_ok = True
        result.final_url = page.final_url

        if seed.seed_type == SourceSeedUrl.SeedType.DIRECT:
            candidates = [page.final_url]
        elif seed.seed_type == SourceSeedUrl.SeedType.LISTING:
            candidates = discover_article_links(
                html=page.text,
                page_url=page.final_url,
            )[:max_links]
            result.discovered_links = len(candidates)
        else:
            result.error = (
                f"Seed type '{seed.seed_type}' belum didukung "
                "GenericHtmlCrawler."
            )
            return result

        valid_candidates: list[str] = []

        for candidate in candidates:
            try:
                normalized = normalize_url(candidate)
            except ValueError:
                continue

            validation = validate_source_url(
                source,
                normalized,
                require_source_ready=False,
            )

            if validation.is_valid:
                valid_candidates.append(
                    validation.normalized_url
                )

        result.valid_article_candidates = len(valid_candidates)

        if not valid_candidates:
            return result

        sample_url = valid_candidates[0]
        result.sample_article_url = sample_url

        try:
            article_page = (
                page
                if (
                    seed.seed_type
                    == SourceSeedUrl.SeedType.DIRECT
                    and normalize_url(page.final_url)
                    == sample_url
                )
                else client.get_html(sample_url)
            )
        except (CrawlerHttpError, RobotsDeniedError) as exc:
            result.error = str(exc)
            return result
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            return result

        parsed = parse_article_html(
            html=article_page.text,
            page_url=article_page.final_url,
        )

        result.sample_title = parsed.title
        result.sample_content_length = len(parsed.content)
        result.parser_ok = bool(
            parsed.title
            and len(parsed.content) >= 100
        )

        if not result.parser_ok:
            result.error = (
                "Parser belum menghasilkan judul dan konten "
                "minimal 100 karakter."
            )

        return result

    def _recommend(
        self,
        *,
        source: Source,
        readiness_ok: bool,
        live_status: str,
        active_seed_count: int,
        active_allow_count: int,
    ) -> str:
        if not source.is_active or not source.is_verified:
            return "KEEP_DISABLED"

        if active_seed_count == 0 or active_allow_count == 0:
            return "NEEDS_CONFIG"

        if live_status == "PASS":
            if readiness_ok:
                return "KEEP_ENABLED"
            return "ENABLE_CRAWLING"

        if live_status in {
            "NO_VALID_ARTICLE_LINK",
            "PARSER_FAILED",
            "SEED_FETCH_FAILED",
        }:
            return "MANUAL_REVIEW"

        if live_status == "SKIPPED":
            return "CONFIG_ONLY"

        return "MANUAL_REVIEW"

    def _build_summary(
        self,
        report: list[dict[str, Any]],
    ) -> dict[str, int]:
        recommendations = [
            item["recommendation"]
            for item in report
        ]

        return {
            "total_sources": len(report),
            "config_ready": sum(
                1
                for item in report
                if item["readiness_ok"]
            ),
            "live_passed": sum(
                1
                for item in report
                if item["live_status"] == "PASS"
            ),
            "needs_config": recommendations.count(
                "NEEDS_CONFIG"
            ),
            "manual_review": recommendations.count(
                "MANUAL_REVIEW"
            ),
            "keep_disabled": recommendations.count(
                "KEEP_DISABLED"
            ),
        }
