from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import replace

import feedparser
from bs4 import BeautifulSoup
from django.conf import settings

from apps.articles.models import Article
from apps.entities.eligibility import (
    evaluate_cheap_filter,
    evaluate_surveillance_eligibility,
)
from apps.ingestion.dto import ArticlePayload
from apps.sources.models import Source, SourceSeedUrl
from apps.sources.services import (
    check_source_seed_readiness,
    is_domain_allowed,
    validate_source_url,
)

from .http_client import (
    CrawlerHttpClient,
    CrawlerHttpError,
    RobotsDeniedError,
)
from .html_parser import clean_text, parse_datetime
from .real_crawler import (
    MINIMUM_ARTICLE_CONTENT_LENGTH,
    GenericHtmlCrawler,
    build_surveillance_metadata,
)
from .results import CrawlItemStatus


logger = logging.getLogger(__name__)


class OfficialRssCrawler(GenericHtmlCrawler):
    """Discovery artikel melalui RSS/Atom resmi milik penerbit.

    RSS hanya menemukan kandidat. URL artikel tetap harus berada pada domain
    ``Source`` yang sama, lolos pola allow/deny, lalu isi lengkapnya diproses
    oleh pipeline HTML yang sudah ada. Dengan demikian RSS tidak menciptakan
    jalur ingestion atau aturan eligibility paralel.
    """

    job_type = "rss"
    discovery_channel = "official_rss"

    def __init__(
        self,
        *,
        source_code: str,
        limit: int | None = None,
        candidate_limit: int | None = None,
    ) -> None:
        super().__init__(
            source_code=source_code,
            limit=limit,
            candidate_limit=candidate_limit,
        )
        self._rss_item_context: dict = {}

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
        super()._record_item(
            original_url=original_url,
            status=status,
            normalized_url=normalized_url,
            title=title,
            reason=reason,
            error_message=error_message,
            metadata={
                **self._rss_item_context,
                **(metadata or {}),
            },
        )

    def _get_source(self) -> Source:
        try:
            source = Source.objects.get(
                code=self.source_code,
            )
        except Source.DoesNotExist as exc:
            raise ValueError(
                "Sumber dengan kode "
                f"'{self.source_code}' tidak ditemukan."
            ) from exc

        readiness = check_source_seed_readiness(
            source,
            seed_types=(SourceSeedUrl.SeedType.RSS,),
        )

        if not readiness.is_ready:
            raise ValueError(
                "Sumber belum siap untuk RSS resmi: "
                + "; ".join(readiness.errors)
            )

        return source

    @staticmethod
    def _entry_url(entry) -> str:
        direct_link = str(entry.get("link") or "").strip()

        if direct_link:
            return direct_link

        for link in entry.get("links", ()):
            href = str(link.get("href") or "").strip()
            rel = str(link.get("rel") or "alternate").casefold()

            if href and rel in {"", "alternate"}:
                return href

        return ""

    @staticmethod
    def _entry_identifier(entry) -> str:
        return str(
            entry.get("id")
            or entry.get("guid")
            or ""
        ).strip()

    @staticmethod
    def _entry_title(entry) -> str:
        return str(entry.get("title") or "").strip()

    @staticmethod
    def _entry_full_content(entry) -> str:
        """Ambil hanya elemen content penuh; summary bukan full article."""
        candidates = [
            str(item.get("value") or "")
            for item in entry.get("content", ())
            if item.get("value")
        ]

        if not candidates:
            return ""

        raw_content = max(candidates, key=len)
        soup = BeautifulSoup(raw_content, "html.parser")

        for node in soup(("script", "style", "noscript")):
            node.decompose()

        return clean_text(soup.get_text(" "))

    @staticmethod
    def _entry_published_at(entry):
        return parse_datetime(
            str(
                entry.get("published")
                or entry.get("updated")
                or ""
            )
        )

    def _payload_from_feed_content(
        self,
        *,
        source: Source,
        entry,
        normalized_url: str,
    ) -> tuple[bool, ArticlePayload | None]:
        """Gunakan full-text resmi bila tersedia.

        Nilai pertama menandai apakah entri sudah ditangani. ``False`` berarti
        feed hanya memiliki ringkasan sehingga crawler harus mengambil HTML
        penerbit. ``True`` dengan payload ``None`` berarti full-text tersedia
        tetapi ditolak oleh validasi yang sama dengan kanal HTML.
        """
        content = self._entry_full_content(entry)

        if len(content) < MINIMUM_ARTICLE_CONTENT_LENGTH:
            return False, None

        title = self._entry_title(entry)

        if not title:
            self._record_item(
                original_url=normalized_url,
                normalized_url=normalized_url,
                status=CrawlItemStatus.REJECTED,
                reason="Judul artikel tidak ditemukan pada entri RSS.",
                metadata={"stage": "rss_full_content_parsing"},
            )
            return True, None

        preview_chars = int(
            getattr(settings, "CRAWLER_CHEAP_FILTER_CHARS", 5000)
        )
        cheap_result = evaluate_cheap_filter(
            title=title,
            content=content,
            preview_chars=preview_chars,
        )

        if not cheap_result.passed:
            self._record_item(
                original_url=normalized_url,
                normalized_url=normalized_url,
                title=title,
                status=CrawlItemStatus.REJECTED,
                reason=cheap_result.reason,
                metadata={
                    "stage": "rss_full_content_cheap_filter",
                    "content_length": len(content),
                },
            )
            return True, None

        eligibility = evaluate_surveillance_eligibility(
            title=title,
            content=content,
            cheap_result=cheap_result,
        )
        surveillance_metadata = build_surveillance_metadata(eligibility)

        self._record_item(
            original_url=normalized_url,
            normalized_url=normalized_url,
            title=title,
            status=CrawlItemStatus.FOUND,
            reason=eligibility.reason,
            metadata={
                "stage": "accepted_from_rss_full_content",
                "content_length": len(content),
            },
        )

        return True, ArticlePayload(
            source_code=source.code,
            url=normalized_url,
            title=title,
            content=content,
            published_at=self._entry_published_at(entry),
            author=str(entry.get("author") or "").strip(),
            metadata={
                "crawler": self.discovery_channel,
                "discovery_channel": self.discovery_channel,
                "retrieval_channel": "official_rss_full_content",
                "surveillance": surveillance_metadata,
            },
        )

    def _with_rss_provenance(
        self,
        payload: ArticlePayload,
        *,
        feed_url: str,
        feed_final_url: str,
        entry_identifier: str,
    ) -> ArticlePayload:
        return replace(
            payload,
            metadata={
                **payload.metadata,
                "crawler": self.discovery_channel,
                "discovery_channel": self.discovery_channel,
                "retrieval_channel": payload.metadata.get(
                    "retrieval_channel",
                    "publisher_html",
                ),
                "rss": {
                    "feed_url": feed_url,
                    "feed_final_url": feed_final_url,
                    "entry_identifier": entry_identifier,
                },
            },
        )

    def _crawl_feed_seed(
        self,
        *,
        source: Source,
        seed: SourceSeedUrl,
        client: CrawlerHttpClient,
        processed_urls: set[str],
        remaining_limit: int,
    ) -> Iterable[ArticlePayload]:
        self._rss_item_context = {
            "discovery_channel": self.discovery_channel,
            "feed_url": seed.url,
            "seed_type": SourceSeedUrl.SeedType.RSS,
        }

        try:
            feed_page = client.get_feed(seed.url)
        except (CrawlerHttpError, RobotsDeniedError) as exc:
            self._record_item(
                original_url=seed.url,
                status=CrawlItemStatus.FAILED,
                reason="RSS/Atom resmi gagal diambil.",
                error_message=str(exc),
                metadata={"stage": "rss_seed_download"},
            )
            return

        if not is_domain_allowed(source, feed_page.final_url):
            self._record_item(
                original_url=seed.url,
                normalized_url=feed_page.final_url,
                status=CrawlItemStatus.REJECTED,
                reason=(
                    "URL hasil redirect feed tidak berada pada domain "
                    "sumber yang diizinkan."
                ),
                metadata={
                    "stage": "rss_seed_redirect_validation",
                    "feed_final_url": feed_page.final_url,
                },
            )
            return

        parsed_feed = feedparser.parse(feed_page.text)
        entries = list(parsed_feed.entries)

        if parsed_feed.bozo and not entries:
            self._record_item(
                original_url=seed.url,
                normalized_url=feed_page.final_url,
                status=CrawlItemStatus.FAILED,
                reason="RSS/Atom tidak dapat diparsing.",
                error_message=str(parsed_feed.bozo_exception),
                metadata={
                    "stage": "rss_feed_parsing",
                    "feed_final_url": feed_page.final_url,
                },
            )
            return

        yielded_count = 0

        for index, entry in enumerate(entries, start=1):
            if yielded_count >= remaining_limit:
                break

            entry_url = self._entry_url(entry)
            entry_title = self._entry_title(entry)
            entry_identifier = self._entry_identifier(entry)
            self._rss_item_context = {
                "discovery_channel": self.discovery_channel,
                "feed_url": seed.url,
                "feed_final_url": feed_page.final_url,
                "seed_type": SourceSeedUrl.SeedType.RSS,
                "entry_identifier": entry_identifier,
                "entry_index": index,
            }

            if not entry_url:
                self._record_item(
                    original_url=f"{seed.url}#entry-{index}",
                    title=entry_title,
                    status=CrawlItemStatus.REJECTED,
                    reason="Entri RSS tidak memiliki URL artikel.",
                    metadata={"stage": "rss_entry_url"},
                )
                continue

            validation = validate_source_url(
                source,
                entry_url,
            )

            if not validation.is_valid:
                self._record_item(
                    original_url=entry_url,
                    normalized_url=validation.normalized_url,
                    title=entry_title,
                    status=CrawlItemStatus.REJECTED,
                    reason=validation.reason,
                    metadata={"stage": "rss_entry_validation"},
                )
                continue

            normalized_url = validation.normalized_url

            if normalized_url in processed_urls:
                self._record_item(
                    original_url=entry_url,
                    normalized_url=normalized_url,
                    title=entry_title,
                    status=CrawlItemStatus.DUPLICATE,
                    reason=(
                        "URL artikel ditemukan lebih dari sekali dalam "
                        "RSS pada proses yang sama."
                    ),
                    metadata={"stage": "rss_entry_deduplication"},
                )
                continue

            processed_urls.add(normalized_url)

            if Article.objects.filter(
                normalized_url=normalized_url,
            ).exists():
                self._record_item(
                    original_url=entry_url,
                    normalized_url=normalized_url,
                    title=entry_title,
                    status=CrawlItemStatus.DUPLICATE,
                    reason=(
                        "Artikel RSS sudah tersimpan dari proses sebelumnya "
                        "dan dilewati tanpa fetch ulang."
                    ),
                    metadata={"stage": "known_article_skip"},
                )
                continue

            if self._candidate_checked >= self._get_candidate_limit():
                break

            self._candidate_checked += 1
            content_handled, payload = self._payload_from_feed_content(
                source=source,
                entry=entry,
                normalized_url=normalized_url,
            )

            if not content_handled:
                payload = self._parse_article(
                    source=source,
                    client=client,
                    article_url=normalized_url,
                )

            if payload is None:
                continue

            yielded_count += 1
            yield self._with_rss_provenance(
                payload,
                feed_url=seed.url,
                feed_final_url=feed_page.final_url,
                entry_identifier=entry_identifier,
            )

    def crawl(self) -> Iterable[ArticlePayload]:
        source = self._get_source()
        self._candidate_checked = 0
        article_limit = self._get_article_limit(source)
        seeds = source.seed_urls.filter(
            is_active=True,
            seed_type=SourceSeedUrl.SeedType.RSS,
        ).order_by("priority", "url")

        processed_urls: set[str] = set()
        total_yielded = 0

        with CrawlerHttpClient(source) as client:
            for seed in seeds:
                if total_yielded >= article_limit:
                    break

                for payload in self._crawl_feed_seed(
                    source=source,
                    seed=seed,
                    client=client,
                    processed_urls=processed_urls,
                    remaining_limit=article_limit - total_yielded,
                ):
                    total_yielded += 1
                    yield payload

                    if total_yielded >= article_limit:
                        break

        self._rss_item_context = {}
        logger.info(
            "RSS resmi selesai source=%s total_payload=%s checked=%s",
            source.code,
            total_yielded,
            self._candidate_checked,
        )
