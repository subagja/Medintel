import logging

from django.conf import settings
from collections.abc import Iterable

from apps.entities.eligibility import (
    evaluate_cheap_filter,
    evaluate_surveillance_eligibility,
)
from apps.ingestion.dto import ArticlePayload
from apps.sources.models import (
    Source,
    SourceSeedUrl,
)
from apps.sources.services import (
    check_source_crawl_readiness,
    normalize_url,
    validate_source_url,
)

from .base import BaseCrawler
from .html_parser import (
    discover_article_links,
    parse_article_html,
)
from .http_client import (
    CrawlerHttpClient,
    CrawlerHttpError,
    RobotsDeniedError,
)
from pathlib import Path
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


MINIMUM_ARTICLE_CONTENT_LENGTH = 100


def _safe_debug_slug(url: str) -> str:
    slug = (
        urlparse(url)
        .path
        .strip("/")
        .replace("/", "_")
    )

    return slug[:180] or "homepage"


def save_debug_html(
    *,
    source_code: str,
    url: str,
    html: str,
    category: str,
) -> None:
    debug_dir = (
        Path("debug_html")
        / category
    )
    debug_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    debug_file = (
        debug_dir
        / (
            f"{source_code}_"
            f"{_safe_debug_slug(url)}.html"
        )
    )

    debug_file.write_text(
        html,
        encoding="utf-8",
    )

    logger.info(
        "HTML debug disimpan path=%s",
        debug_file,
    )


class GenericHtmlCrawler(BaseCrawler):
    """
    Crawler HTML generik.

    Tugas crawler:
    1. Membuka seed URL.
    2. Menemukan link artikel.
    3. Memvalidasi URL.
    4. Membuka halaman artikel.
    5. Mengekstrak data artikel.
    6. Menghasilkan ArticlePayload.

    Crawler tidak menyimpan langsung ke database.
    Penyimpanan dilakukan oleh run_crawler().
    """

    def __init__(
        self,
        *,
        source_code: str,
        limit: int | None = None,
    ) -> None:
        self.source_code = source_code
        self.limit = limit

    def _get_source(self) -> Source:
        try:
            source = Source.objects.get(
                code=self.source_code,
            )
        except Source.DoesNotExist as exc:
            raise ValueError(
                (
                    "Sumber dengan kode "
                    f"'{self.source_code}' tidak ditemukan."
                )
            ) from exc

        readiness = check_source_crawl_readiness(
            source
        )

        if not readiness.is_ready:
            raise ValueError(
                "Sumber belum siap dicrawl: "
                + "; ".join(readiness.errors)
            )

        return source

    def _get_article_limit(
        self,
        source: Source,
    ) -> int:
        configured_limit = (
            source.max_articles_per_run
        )

        if self.limit is None:
            return configured_limit

        if self.limit < 1:
            raise ValueError(
                "Limit crawler minimal bernilai 1."
            )

        return min(
            self.limit,
            configured_limit,
        )

    def _parse_article(
        self,
        *,
        source: Source,
        client: CrawlerHttpClient,
        article_url: str,
    ) -> ArticlePayload | None:
        validation = validate_source_url(
            source,
            article_url,
        )

        if not validation.is_valid:
            logger.info(
                (
                    "URL ditolak "
                    "source=%s url=%s reason=%s"
                ),
                source.code,
                article_url,
                validation.reason,
            )

            return None

        try:
            page = client.get_html(
                validation.normalized_url
            )
        except (
            CrawlerHttpError,
            RobotsDeniedError,
        ) as exc:
            logger.warning(
                (
                    "Gagal mengambil artikel "
                    "source=%s url=%s error=%s"
                ),
                source.code,
                validation.normalized_url,
                exc,
            )

            return None

        redirected_url_validation = (
            validate_source_url(
                source,
                page.final_url,
            )
        )

        if not redirected_url_validation.is_valid:
            logger.info(
                (
                    "URL hasil redirect ditolak "
                    "source=%s url=%s reason=%s"
                ),
                source.code,
                page.final_url,
                redirected_url_validation.reason,
            )

            return None

        parsed = parse_article_html(
            html=page.text,
            page_url=page.final_url,
        )

        if not parsed.title:
            logger.warning(
                (
                    "Judul artikel tidak ditemukan "
                    "source=%s url=%s"
                ),
                source.code,
                page.final_url,
            )

            return None

        if (
            len(parsed.content)
            < MINIMUM_ARTICLE_CONTENT_LENGTH
        ):
            logger.warning(
                (
                    "Isi artikel terlalu pendek "
                    "source=%s url=%s length=%s"
                ),
                source.code,
                page.final_url,
                len(parsed.content),
            )

            return None

        preview_chars = int(
            getattr(
                settings,
                "CRAWLER_CHEAP_FILTER_CHARS",
                5000,
            )
        )

        cheap_result = evaluate_cheap_filter(
            title=parsed.title,
            content=parsed.content,
            preview_chars=preview_chars,
        )

        if not cheap_result.passed:
            logger.info(
                (
                    "Artikel ditolak filter murah "
                    "source=%s url=%s reason=%s"
                ),
                source.code,
                page.final_url,
                cheap_result.reason,
            )

            if getattr(
                settings,
                "CRAWLER_SAVE_REJECTED_DEBUG_HTML",
                False,
            ):
                save_debug_html(
                    source_code=source.code,
                    url=page.final_url,
                    html=page.text,
                    category="rejected",
                )

            return None

        eligibility = evaluate_surveillance_eligibility(
            title=parsed.title,
            content=parsed.content,
            cheap_result=cheap_result,
        )

        if not eligibility.is_eligible:
            logger.info(
                (
                    "Artikel ditolak filter lengkap "
                    "source=%s url=%s reason=%s"
                ),
                source.code,
                page.final_url,
                eligibility.reason,
            )

            if getattr(
                settings,
                "CRAWLER_SAVE_REJECTED_DEBUG_HTML",
                False,
            ):
                save_debug_html(
                    source_code=source.code,
                    url=page.final_url,
                    html=page.text,
                    category="rejected",
                )

            return None

        if getattr(
            settings,
            "CRAWLER_SAVE_ACCEPTED_DEBUG_HTML",
            False,
        ):
            save_debug_html(
                source_code=source.code,
                url=page.final_url,
                html=page.text,
                category="accepted",
            )

        canonical_url = (
            parsed.canonical_url
            or page.final_url
        )

        canonical_validation = (
            validate_source_url(
                source,
                canonical_url,
            )
        )

        if canonical_validation.is_valid:
            final_article_url = (
                canonical_validation.normalized_url
            )
        else:
            final_article_url = (
                redirected_url_validation.normalized_url
            )

        surveillance_metadata = {
            "is_relevant": True,
            "reason": eligibility.reason,
            "diseases": [
                {
                    "disease_id": mention.disease_id,
                    "disease_name": mention.disease_name,
                    "matched_text": mention.matched_text,
                }
                for mention in eligibility.disease_mentions
            ],
            "counts": [
                {
                    "value": mention.value,
                    "matched_text": mention.matched_text,
                }
                for mention in eligibility.count_mentions
            ],
            "locations": [
                {
                    "location_id": mention.location_id,
                    "location_name": mention.location_name,
                    "matched_text": mention.matched_text,
                }
                for mention in eligibility.location_mentions
            ],
            "evidence_text": eligibility.evidence_text,
        }

        return ArticlePayload(
            source_code=source.code,
            url=final_article_url,
            title=parsed.title,
            content=parsed.content,
            published_at=parsed.published_at,
            author=parsed.author,
            metadata={
                **parsed.metadata,
                "crawler": "generic_html",
                "requested_url": article_url,
                "final_url": page.final_url,
                "canonical_url": canonical_url,
                "surveillance": (
                    surveillance_metadata
                ),
            },
        )

    def _crawl_direct_seed(
        self,
        *,
        source: Source,
        seed: SourceSeedUrl,
        client: CrawlerHttpClient,
    ) -> ArticlePayload | None:
        return self._parse_article(
            source=source,
            client=client,
            article_url=seed.url,
        )

    def _crawl_listing_seed(
        self,
        *,
        source: Source,
        seed: SourceSeedUrl,
        client: CrawlerHttpClient,
        processed_urls: set[str],
        remaining_limit: int,
    ) -> Iterable[ArticlePayload]:
        logger.info(
            (
                "Membuka seed listing "
                "source=%s url=%s"
            ),
            source.code,
            seed.url,
        )

        try:
            listing_page = client.get_html(
                seed.url
            )
        except (
            CrawlerHttpError,
            RobotsDeniedError,
        ) as exc:
            logger.error(
                (
                    "Gagal mengambil seed listing "
                    "source=%s url=%s error=%s"
                ),
                source.code,
                seed.url,
                exc,
            )

            return

        logger.info(
            (
                "Seed listing berhasil dibuka "
                "source=%s requested=%s final=%s"
            ),
            source.code,
            seed.url,
            listing_page.final_url,
        )

        discovered_links = discover_article_links(
            html=listing_page.text,
            page_url=listing_page.final_url,
        )

        logger.info(
            (
                "Link ditemukan dari seed "
                "source=%s count=%s"
            ),
            source.code,
            len(discovered_links),
        )

        yielded_count = 0
        rejected_count = 0
        duplicate_candidate_count = 0
        invalid_url_count = 0

        for discovered_url in discovered_links:
            if yielded_count >= remaining_limit:
                logger.info(
                    (
                        "Batas artikel tercapai "
                        "source=%s limit=%s"
                    ),
                    source.code,
                    remaining_limit,
                )
                break

            try:
                normalized_candidate = normalize_url(
                    discovered_url
                )
            except ValueError as exc:
                invalid_url_count += 1

                logger.debug(
                    (
                        "URL kandidat tidak valid "
                        "url=%s error=%s"
                    ),
                    discovered_url,
                    exc,
                )

                continue

            if normalized_candidate in processed_urls:
                duplicate_candidate_count += 1
                continue

            processed_urls.add(
                normalized_candidate
            )

            validation = validate_source_url(
                source,
                normalized_candidate,
            )

            if not validation.is_valid:
                rejected_count += 1

                logger.debug(
                    (
                        "Link kandidat ditolak "
                        "source=%s url=%s reason=%s"
                    ),
                    source.code,
                    normalized_candidate,
                    validation.reason,
                )

                continue

            logger.info(
                (
                    "Link kandidat diizinkan "
                    "source=%s url=%s"
                ),
                source.code,
                validation.normalized_url,
            )

            payload = self._parse_article(
                source=source,
                client=client,
                article_url=validation.normalized_url,
            )

            if payload is None:
                logger.info(
                    (
                        "Artikel tidak menghasilkan payload "
                        "source=%s url=%s"
                    ),
                    source.code,
                    validation.normalized_url,
                )

                continue

            yielded_count += 1

            logger.info(
                (
                    "Artikel diterima crawler "
                    "source=%s title=%s"
                ),
                source.code,
                payload.title,
            )

            yield payload

        logger.info(
            (
                "Ringkasan seed listing "
                "source=%s discovered=%s "
                "yielded=%s rejected=%s "
                "duplicate_candidates=%s invalid_urls=%s"
            ),
            source.code,
            len(discovered_links),
            yielded_count,
            rejected_count,
            duplicate_candidate_count,
            invalid_url_count,
        )

    def crawl(self) -> Iterable[ArticlePayload]:
        source = self._get_source()

        article_limit = self._get_article_limit(
            source
        )

        seeds = source.seed_urls.filter(
            is_active=True,
        ).order_by(
            "priority",
            "url",
        )

        seed_count = seeds.count()

        logger.info(
            (
                "Crawler dimulai "
                "source=%s strategy=%s "
                "seed_count=%s limit=%s"
            ),
            source.code,
            source.crawl_strategy,
            seed_count,
            article_limit,
        )

        if seed_count == 0:
            logger.warning(
                (
                    "Tidak ada seed URL aktif "
                    "source=%s"
                ),
                source.code,
            )

            return

        processed_urls: set[str] = set()
        total_yielded = 0

        with CrawlerHttpClient(
            source
        ) as client:
            for seed in seeds:
                if total_yielded >= article_limit:
                    break

                logger.info(
                    (
                        "Memproses seed "
                        "source=%s type=%s url=%s"
                    ),
                    source.code,
                    seed.seed_type,
                    seed.url,
                )

                if (
                    seed.seed_type
                    == SourceSeedUrl.SeedType.DIRECT
                ):
                    payload = self._crawl_direct_seed(
                        source=source,
                        seed=seed,
                        client=client,
                    )

                    if payload is not None:
                        total_yielded += 1

                        logger.info(
                            (
                                "Direct seed menghasilkan artikel "
                                "source=%s title=%s"
                            ),
                            source.code,
                            payload.title,
                        )

                        yield payload
                    else:
                        logger.info(
                            (
                                "Direct seed tidak menghasilkan artikel "
                                "source=%s url=%s"
                            ),
                            source.code,
                            seed.url,
                        )

                    continue

                if (
                    seed.seed_type
                    == SourceSeedUrl.SeedType.LISTING
                ):
                    remaining_limit = (
                        article_limit
                        - total_yielded
                    )

                    for payload in self._crawl_listing_seed(
                        source=source,
                        seed=seed,
                        client=client,
                        processed_urls=processed_urls,
                        remaining_limit=remaining_limit,
                    ):
                        total_yielded += 1

                        yield payload

                        if (
                            total_yielded
                            >= article_limit
                        ):
                            break

                    continue

                logger.warning(
                    (
                        "Seed type belum didukung "
                        "source=%s type=%s url=%s"
                    ),
                    source.code,
                    seed.seed_type,
                    seed.url,
                )

        logger.info(
            (
                "Crawler selesai "
                "source=%s total_payload=%s"
            ),
            source.code,
            total_yielded,
        )