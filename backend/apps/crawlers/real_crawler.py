import logging

from django.conf import settings
from collections.abc import Iterable

from apps.entities.eligibility import (
    evaluate_cheap_filter,
    evaluate_surveillance_eligibility,
    extract_disease_mentions,
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
    ArticleLinkCandidate,
    discover_article_links,
    parse_article_html,
)
from .http_client import (
    CrawlerHttpClient,
    CrawlerHttpError,
    RobotsDeniedError,
)
from .results import (
    CrawlItemEvent,
    CrawlItemStatus,
)
from pathlib import Path
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


MINIMUM_ARTICLE_CONTENT_LENGTH = 100


STRONG_EPIDEMIOLOGICAL_TERMS = (
    "wabah",
    "klb",
    "outbreak",
    "terinfeksi",
    "penularan",
    "dinas kesehatan",
    "kementerian kesehatan",
)


def listing_candidate_is_relevant(
    candidate: ArticleLinkCandidate,
) -> tuple[bool, str]:
    url_path = (
        urlparse(candidate.url)
        .path
        .replace("-", " ")
        .replace("_", " ")
    )

    listing_text = " ".join(
        value
        for value in (
            candidate.anchor_text,
            candidate.context_text,
            url_path,
        )
        if value
    ).strip()

    if not listing_text:
        return (
            False,
            "Teks kandidat listing kosong.",
        )

    disease_mentions = extract_disease_mentions(
        listing_text
    )

    if disease_mentions:
        names = ", ".join(
            sorted(
                {
                    mention.disease_name
                    for mention in disease_mentions
                }
            )
        )

        return (
            True,
            f"Penyakit terdeteksi pada listing: {names}.",
        )

    normalized = listing_text.casefold()

    strong_matches = [
        term
        for term in STRONG_EPIDEMIOLOGICAL_TERMS
        if term in normalized
    ]

    if strong_matches:
        return (
            True,
            (
                "Istilah epidemiologis kuat terdeteksi: "
                + ", ".join(strong_matches[:5])
                + "."
            ),
        )

    return (
        False,
        (
            "Tidak ada penyakit atau istilah "
            "epidemiologis kuat pada listing."
        ),
    )


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


def _mention_terms(mention) -> list[str]:
    matched_terms = getattr(
        mention,
        "matched_terms",
        None,
    )

    if isinstance(matched_terms, str):
        return [matched_terms]

    if matched_terms:
        return list(matched_terms)

    matched_text = getattr(
        mention,
        "matched_text",
        "",
    )

    return [matched_text] if matched_text else []


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
        candidate_limit: int | None = None,
    ) -> None:
        self.source_code = source_code
        self.limit = limit
        self.candidate_limit = candidate_limit
        self._candidate_checked = 0

    def _record_item(
        self,
        *,
        original_url: str,
        status: str,
        normalized_url: str = "",
        title: str = "",
        reason: str = "",
        error_message: str = "",
        metadata: dict | None = None,
    ) -> None:
        self.emit_item_event(
            CrawlItemEvent(
                original_url=original_url,
                normalized_url=normalized_url,
                title=title,
                status=status,
                reason=reason,
                error_message=error_message,
                metadata=metadata or {},
            )
        )

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

    def _get_candidate_limit(self) -> int:
        configured_limit = int(
            getattr(
                settings,
                "CRAWLER_CANDIDATE_LIMIT",
                30,
            )
        )

        if self.candidate_limit is None:
            return max(configured_limit, 1)

        if self.candidate_limit < 1:
            raise ValueError(
                "Candidate limit minimal bernilai 1."
            )

        return self.candidate_limit

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
            self._record_item(
                original_url=article_url,
                normalized_url=(
                    validation.normalized_url
                ),
                status=CrawlItemStatus.REJECTED,
                reason=validation.reason,
                metadata={
                    "stage": "url_validation",
                },
            )

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
            self._record_item(
                original_url=article_url,
                normalized_url=(
                    validation.normalized_url
                ),
                status=CrawlItemStatus.FAILED,
                reason=(
                    "Halaman artikel gagal diambil."
                ),
                error_message=str(exc),
                metadata={
                    "stage": "article_download",
                },
            )

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
            self._record_item(
                original_url=article_url,
                normalized_url=page.final_url,
                status=CrawlItemStatus.REJECTED,
                reason=(
                    "URL hasil redirect ditolak: "
                    + redirected_url_validation.reason
                ),
                metadata={
                    "stage": "redirect_validation",
                    "final_url": page.final_url,
                },
            )

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
            self._record_item(
                original_url=article_url,
                normalized_url=page.final_url,
                status=CrawlItemStatus.REJECTED,
                reason="Judul artikel tidak ditemukan.",
                metadata={
                    "stage": "article_parsing",
                },
            )

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
            self._record_item(
                original_url=article_url,
                normalized_url=page.final_url,
                title=parsed.title,
                status=CrawlItemStatus.REJECTED,
                reason=(
                    "Isi artikel terlalu pendek "
                    f"({len(parsed.content)} karakter)."
                ),
                metadata={
                    "stage": "article_parsing",
                    "content_length": len(parsed.content),
                },
            )

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
            self._record_item(
                original_url=article_url,
                normalized_url=page.final_url,
                title=parsed.title,
                status=CrawlItemStatus.REJECTED,
                reason=cheap_result.reason,
                metadata={
                    "stage": "cheap_filter",
                },
            )

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
            self._record_item(
                original_url=article_url,
                normalized_url=page.final_url,
                title=parsed.title,
                status=CrawlItemStatus.REJECTED,
                reason=eligibility.reason,
                metadata={
                    "stage": "full_filter",
                },
            )

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
                    "matched_terms": _mention_terms(mention),
                }
                for mention in eligibility.disease_mentions
            ],
            "counts": [
                {
                    "value": mention.value,
                    "matched_text": mention.matched_text,
                    "metric_type": mention.metric_type,
                }
                for mention in eligibility.count_mentions
            ],
            "locations": [
                {
                    "location_id": mention.location_id,
                    "location_name": mention.location_name,
                    "matched_text": mention.matched_text,
                    "administrative_level": (
                        mention.administrative_level
                    ),
                    "country_code": mention.country_code,
                    "confidence_score": (
                        mention.confidence_score
                    ),
                    "is_primary": mention.is_primary,
                    "latitude": mention.latitude,
                    "longitude": mention.longitude,
                }
                for mention in eligibility.location_mentions
            ],
            "geographic_scope": "domestic",
            "evidence_text": eligibility.evidence_text,
        }

        self._record_item(
            original_url=final_article_url,
            normalized_url=final_article_url,
            title=parsed.title,
            status=CrawlItemStatus.FOUND,
            reason=eligibility.reason,
            metadata={
                "stage": "accepted_for_ingestion",
                "requested_url": article_url,
                "final_url": page.final_url,
            },
        )

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
            self._record_item(
                original_url=seed.url,
                status=CrawlItemStatus.FAILED,
                reason=(
                    "Halaman daftar artikel gagal diambil."
                ),
                error_message=str(exc),
                metadata={
                    "stage": "seed_download",
                    "seed_type": seed.seed_type,
                },
            )

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
        prefilter_rejected_count = 0
        duplicate_candidate_count = 0
        invalid_url_count = 0

        for candidate in discovered_links:
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
                    candidate.url
                )
            except ValueError as exc:
                invalid_url_count += 1

                self._record_item(
                    original_url=candidate.url,
                    status=CrawlItemStatus.REJECTED,
                    reason=str(exc),
                    metadata={
                        "stage": "candidate_url_normalization",
                        "seed_url": seed.url,
                    },
                )

                logger.debug(
                    (
                        "URL kandidat tidak valid "
                        "url=%s error=%s"
                    ),
                    candidate.url,
                    exc,
                )
                continue

            if normalized_candidate in processed_urls:
                duplicate_candidate_count += 1

                self._record_item(
                    original_url=candidate.url,
                    normalized_url=normalized_candidate,
                    title=candidate.anchor_text,
                    status=CrawlItemStatus.DUPLICATE,
                    reason=(
                        "URL kandidat ditemukan lebih dari sekali "
                        "dalam proses yang sama."
                    ),
                    metadata={
                        "stage": "candidate_deduplication",
                        "seed_url": seed.url,
                    },
                )

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

                self._record_item(
                    original_url=candidate.url,
                    normalized_url=normalized_candidate,
                    title=candidate.anchor_text,
                    status=CrawlItemStatus.REJECTED,
                    reason=validation.reason,
                    metadata={
                        "stage": "candidate_url_validation",
                        "seed_url": seed.url,
                    },
                )

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

            prefilter_passed, prefilter_reason = (
                listing_candidate_is_relevant(
                    candidate
                )
            )

            if not prefilter_passed:
                prefilter_rejected_count += 1

                self._record_item(
                    original_url=candidate.url,
                    normalized_url=(
                        validation.normalized_url
                    ),
                    title=candidate.anchor_text,
                    status=CrawlItemStatus.REJECTED,
                    reason=prefilter_reason,
                    metadata={
                        "stage": "listing_prefilter",
                        "seed_url": seed.url,
                        "context_text": (
                            candidate.context_text[:500]
                        ),
                    },
                )

                logger.debug(
                    (
                        "Link ditolak prefilter listing "
                        "source=%s url=%s reason=%s"
                    ),
                    source.code,
                    validation.normalized_url,
                    prefilter_reason,
                )
                continue

            if (
                self._candidate_checked
                >= self._get_candidate_limit()
            ):
                logger.info(
                    (
                        "Batas kandidat tercapai "
                        "source=%s candidate_limit=%s"
                    ),
                    source.code,
                    self._get_candidate_limit(),
                )
                break

            # Counter bertambah hanya untuk URL valid yang
            # benar-benar akan diunduh.
            self._candidate_checked += 1

            logger.info(
                (
                    "Link lolos prefilter listing "
                    "source=%s checked=%s/%s "
                    "url=%s reason=%s"
                ),
                source.code,
                self._candidate_checked,
                self._get_candidate_limit(),
                validation.normalized_url,
                prefilter_reason,
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
                "prefilter_rejected=%s checked=%s "
                "duplicate_candidates=%s invalid_urls=%s"
            ),
            source.code,
            len(discovered_links),
            yielded_count,
            rejected_count,
            prefilter_rejected_count,
            self._candidate_checked,
            duplicate_candidate_count,
            invalid_url_count,
        )

    def crawl(self) -> Iterable[ArticlePayload]:
        source = self._get_source()
        self._candidate_checked = 0

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
                "seed_count=%s limit=%s candidate_limit=%s"
            ),
            source.code,
            source.crawl_strategy,
            seed_count,
            article_limit,
            self._get_candidate_limit(),
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

                self._record_item(
                    original_url=seed.url,
                    status=CrawlItemStatus.REJECTED,
                    reason=(
                        "Jenis URL awal belum didukung oleh "
                        "GenericHtmlCrawler."
                    ),
                    metadata={
                        "stage": "seed_dispatch",
                        "seed_type": seed.seed_type,
                    },
                )

        logger.info(
            (
                "Crawler selesai "
                "source=%s total_payload=%s"
            ),
            source.code,
            total_yielded,
        )
