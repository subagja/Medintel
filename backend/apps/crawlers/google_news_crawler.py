from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit

import feedparser
from bs4 import BeautifulSoup
from django.conf import settings
from django.utils import timezone

from apps.articles.models import Article
from apps.ingestion.dto import ArticlePayload
from apps.sources.models import Source, SourceUrlPattern
from apps.sources.services import (
    is_domain_allowed,
    normalize_url,
    validate_source_url,
)

from .google_news_queries import (
    GoogleNewsQueryBatch,
    build_google_news_disease_scope,
)
from .google_news_resolver import (
    build_google_news_rpc_form,
    decode_legacy_google_news_url,
    extract_google_news_article_token,
    extract_google_news_decoding_params,
    extract_publisher_url_from_rpc_response,
)
from .http_client import (
    CrawlerHttpClient,
    CrawlerHttpError,
    HttpPage,
    RobotsDeniedError,
)
from .results import CrawlItemStatus
from .rss_crawler import OfficialRssCrawler


logger = logging.getLogger(__name__)

GOOGLE_NEWS_FEED_BASE = "https://news.google.com/rss/search"
GOOGLE_NEWS_HOSTS = {"news.google.com"}
GOOGLE_NEWS_FEED_PATHS = ("/rss/search",)
GOOGLE_NEWS_ARTICLE_PATHS = ("/rss/articles/", "/articles/", "/read/")
GOOGLE_NEWS_RPC_ENDPOINT = (
    "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je"
)
GOOGLE_NEWS_RPC_PATHS = ("/_/DotsSplashUi/data/batchexecute",)
DEFAULT_GOOGLE_NEWS_MAX_AGE_DAYS = 7
DEFAULT_GOOGLE_NEWS_ARTICLE_LIMIT = 10


@dataclass(frozen=True)
class GoogleNewsPublisherReadiness:
    is_ready: bool
    errors: tuple[str, ...]


@dataclass(frozen=True)
class _GoogleNewsFeedBatch:
    """Feed dan entri satu batch yang siap dijadwalkan round-robin."""

    query_batch: GoogleNewsQueryBatch
    feed_url: str
    entries: tuple[tuple[int, object], ...]


def get_google_news_max_age_days() -> int:
    """Kebijakan umur kanal global; bukan konfigurasi milik satu Source."""
    try:
        value = int(
            getattr(
                settings,
                "GOOGLE_NEWS_MAX_AGE_DAYS",
                DEFAULT_GOOGLE_NEWS_MAX_AGE_DAYS,
            )
        )
    except (TypeError, ValueError):
        value = DEFAULT_GOOGLE_NEWS_MAX_AGE_DAYS
    return min(max(value, 1), 30)


def check_google_news_publisher_readiness(
    source: Source,
) -> GoogleNewsPublisherReadiness:
    """Nilai apakah Source boleh menerima hasil discovery global."""
    errors: list[str] = []
    if not source.is_active:
        errors.append("Sumber tidak aktif.")
    if not source.is_verified:
        errors.append("Sumber belum diverifikasi.")
    if not source.crawl_enabled:
        errors.append("Crawling belum diaktifkan.")
    if not any(
        pattern.is_active
        and pattern.pattern_type == SourceUrlPattern.PatternType.ALLOW
        for pattern in source.url_patterns.all()
    ):
        errors.append("Sumber belum memiliki aturan URL allow.")
    if source.request_delay_seconds < 0:
        errors.append("Jeda permintaan tidak boleh negatif.")
    if source.request_timeout_seconds < 1:
        errors.append("Timeout permintaan minimal adalah 1 detik.")
    return GoogleNewsPublisherReadiness(
        is_ready=not errors,
        errors=tuple(errors),
    )


def get_google_news_allowed_sources() -> list[Source]:
    """Whitelist penerbit untuk discovery berdasarkan Source database."""
    candidates = Source.objects.prefetch_related("url_patterns").order_by(
        "name"
    )
    return [
        source
        for source in candidates
        if check_google_news_publisher_readiness(source).is_ready
    ]


class _CachedPublisherClient:
    """Berikan respons publisher yang sudah didapat saat resolve."""

    def __init__(self, page: HttpPage) -> None:
        self.page = page

    def get_html(self, _url: str) -> HttpPage:
        return self.page


class GoogleNewsRssCrawler(OfficialRssCrawler):
    """Discovery global yang memetakan setiap hasil ke Source penerbit.

    Google News bukan Source dan pencarian tidak dibatasi ``site:domain``.
    URL agregator diselesaikan lebih dahulu. URL penerbit baru diproses bila
    cocok dengan Source aktif/terverifikasi/crawl-enabled serta lolos pola
    allow/deny Source tersebut.
    """

    job_type = "google_news"
    discovery_channel = "google_news_rss"
    global_discovery = True

    def __init__(
        self,
        *,
        limit: int | None = None,
        candidate_limit: int | None = None,
        max_age_days: int | None = None,
        allowed_source_codes: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        super().__init__(
            source_code="",
            limit=limit,
            candidate_limit=candidate_limit,
        )
        self.max_age_days = (
            get_google_news_max_age_days()
            if max_age_days is None
            else min(max(int(max_age_days), 1), 30)
        )
        self.allowed_source_codes = (
            tuple(dict.fromkeys(allowed_source_codes))
            if allowed_source_codes is not None
            else None
        )
        self._execution_metrics: dict[str, int | str] = {}

    def _get_global_article_limit(self) -> int:
        configured = int(
            getattr(
                settings,
                "GOOGLE_NEWS_ARTICLE_LIMIT",
                DEFAULT_GOOGLE_NEWS_ARTICLE_LIMIT,
            )
        )
        configured = min(max(configured, 1), 1000)
        if self.limit is None:
            return configured
        if self.limit < 1:
            raise ValueError("Limit crawler minimal bernilai 1.")
        return min(self.limit, 1000)

    @staticmethod
    def build_feed_url(
        *,
        query_text: str,
        max_age_days: int | None = None,
    ) -> str:
        effective_query = (query_text or "").strip()
        if not effective_query:
            raise ValueError("Kueri Google News tidak boleh kosong.")
        effective_max_age = (
            get_google_news_max_age_days()
            if max_age_days is None
            else min(max(int(max_age_days), 1), 30)
        )
        effective_query = f"({effective_query}) when:{effective_max_age}d"
        params = urlencode(
            {
                "q": effective_query,
                "hl": "id",
                "gl": "ID",
                "ceid": "ID:id",
            }
        )
        return f"{GOOGLE_NEWS_FEED_BASE}?{params}"

    @staticmethod
    def _is_google_news_url(url: str) -> bool:
        hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
        return hostname in GOOGLE_NEWS_HOSTS

    @staticmethod
    def _entry_source_hint(entry) -> dict:
        source = entry.get("source") or {}
        return {
            "title": str(source.get("title") or "").strip(),
            "url": str(source.get("href") or "").strip(),
        }

    @classmethod
    def _publisher_link_from_google_page(cls, page: HttpPage) -> str:
        """Ambil URL eksternal eksplisit tanpa mendekode token privat."""
        candidates: list[str] = []
        query_values = parse_qs(urlsplit(page.final_url).query)
        for key in ("url", "u", "q"):
            candidates.extend(query_values.get(key, ()))

        soup = BeautifulSoup(page.text or "", "html.parser")
        candidates.extend(
            urljoin(page.final_url, str(node.get("href") or "").strip())
            for node in soup.find_all("a", href=True)
        )

        for candidate in candidates:
            candidate = unquote(str(candidate or "").strip())
            if not candidate.startswith(("http://", "https://")):
                continue
            if cls._is_google_news_url(candidate):
                continue
            return candidate
        return ""

    def _publisher_link_from_google_token(
        self,
        *,
        discovery_client: CrawlerHttpClient,
        entry_url: str,
        initial_page: HttpPage,
    ) -> tuple[str, str]:
        """Selesaikan token lama secara lokal dan token baru melalui RPC."""
        legacy_url = decode_legacy_google_news_url(entry_url)
        if legacy_url:
            return legacy_url, "legacy_token_local"

        params_page = initial_page
        params_url = entry_url
        params = extract_google_news_decoding_params(
            article_url=params_url,
            html=params_page.text,
        )

        token = extract_google_news_article_token(entry_url)
        if not token:
            raise CrawlerHttpError(
                "URL Google News tidak memiliki token artikel yang valid."
            )

        # Beberapa bentuk /rss/articles/ tidak menyertakan atribut decoder,
        # sedangkan halaman kanonik /articles/ menyertakannya.
        if params is None:
            canonical_url = f"https://news.google.com/articles/{token}"
            params_page = discovery_client.resolve_discovery_url(
                canonical_url,
                allowed_hosts=GOOGLE_NEWS_HOSTS,
                allowed_path_prefixes=GOOGLE_NEWS_ARTICLE_PATHS,
            )
            if not self._is_google_news_url(params_page.final_url):
                return params_page.final_url, "canonical_http_redirect"

            explicit_url = self._publisher_link_from_google_page(params_page)
            if explicit_url:
                return explicit_url, "canonical_page_link"

            params_url = canonical_url
            params = extract_google_news_decoding_params(
                article_url=params_url,
                html=params_page.text,
            )

        if params is None:
            raise CrawlerHttpError(
                "Halaman Google News tidak memuat signature dan timestamp "
                "untuk mengurai token artikel."
            )

        rpc_page = discovery_client.post_discovery_form(
            GOOGLE_NEWS_RPC_ENDPOINT,
            data=build_google_news_rpc_form(params),
            allowed_hosts=GOOGLE_NEWS_HOSTS,
            allowed_path_prefixes=GOOGLE_NEWS_RPC_PATHS,
            referer=params_page.final_url or params_url,
        )
        publisher_url = extract_publisher_url_from_rpc_response(rpc_page.text)
        if not publisher_url or self._is_google_news_url(publisher_url):
            raise CrawlerHttpError(
                "RPC Google News tidak mengembalikan URL penerbit yang valid."
            )
        return publisher_url, "google_news_article_rpc"

    def _is_stale(self, entry) -> bool:
        published_at = OfficialRssCrawler._entry_published_at(entry)
        if published_at is None:
            return False
        cutoff = timezone.now() - timedelta(days=self.max_age_days)
        return published_at < cutoff

    @staticmethod
    def _registered_source_for_url(
        url: str,
        allowed_sources: Sequence[Source],
    ) -> Source | None:
        # Domain paling spesifik dipilih lebih dahulu bila ada parent/subdomain.
        for source in sorted(
            allowed_sources,
            key=lambda item: len(item.domain or ""),
            reverse=True,
        ):
            if is_domain_allowed(source, url):
                return source
        return None

    def _record_unresolved(
        self,
        *,
        entry_url: str,
        entry_title: str,
        reason: str,
        metadata: dict,
    ) -> None:
        self._record_item(
            original_url=entry_url,
            title=entry_title,
            status=CrawlItemStatus.METADATA_ONLY,
            reason=reason,
            metadata={
                "stage": "publisher_url_resolution",
                "collection_state": "metadata_only",
                **metadata,
            },
        )

    def _resolve_publisher_page(
        self,
        *,
        discovery_client: CrawlerHttpClient,
        allowed_sources: Sequence[Source],
        entry_url: str,
        entry_title: str,
        metadata: dict,
    ) -> tuple[str, HttpPage | None, Source | None]:
        """Resolve agregator, petakan Source, baru ambil halaman penerbit."""
        publisher_url = entry_url
        resolver_method = "direct_publisher_url"
        if self._is_google_news_url(entry_url):
            try:
                page = discovery_client.resolve_discovery_url(
                    entry_url,
                    allowed_hosts=GOOGLE_NEWS_HOSTS,
                    allowed_path_prefixes=GOOGLE_NEWS_ARTICLE_PATHS,
                )
            except (CrawlerHttpError, RobotsDeniedError) as exc:
                self._record_unresolved(
                    entry_url=entry_url,
                    entry_title=entry_title,
                    reason=(
                        "URL Google News belum dapat diselesaikan ke URL "
                        "penerbit; kandidat disimpan sebagai metadata saja."
                    ),
                    metadata={**metadata, "resolver_error": str(exc)},
                )
                return "", None, None

            publisher_url = page.final_url
            resolver_method = "http_redirect"
            if self._is_google_news_url(publisher_url):
                publisher_url = self._publisher_link_from_google_page(page)
                resolver_method = "google_page_link"
                if not publisher_url:
                    try:
                        publisher_url, resolver_method = (
                            self._publisher_link_from_google_token(
                                discovery_client=discovery_client,
                                entry_url=entry_url,
                                initial_page=page,
                            )
                        )
                    except (CrawlerHttpError, RobotsDeniedError) as exc:
                        self._record_unresolved(
                            entry_url=entry_url,
                            entry_title=entry_title,
                            reason=(
                                "Token Google News belum dapat diurai ke URL "
                                "penerbit; kandidat disimpan sebagai metadata."
                            ),
                            metadata={
                                **metadata,
                                "resolver_status_code": page.status_code,
                                "resolver_final_url": page.final_url,
                                "resolver_error": str(exc),
                            },
                        )
                        return "", None, None

        metadata["resolver_method"] = resolver_method

        self._execution_metrics["publisher_urls_resolved"] += 1

        mapped_source = self._registered_source_for_url(
            publisher_url,
            allowed_sources,
        )
        if mapped_source is None:
            self._record_item(
                original_url=entry_url,
                normalized_url=publisher_url,
                title=entry_title,
                status=CrawlItemStatus.REJECTED,
                reason=(
                    "URL penerbit tidak cocok dengan Source yang aktif, "
                    "terverifikasi, dan diizinkan untuk crawling."
                ),
                metadata={
                    "stage": "publisher_source_mapping",
                    **metadata,
                },
            )
            return "", None, None

        validation = validate_source_url(mapped_source, publisher_url)
        if not validation.is_valid:
            self._record_item(
                original_url=entry_url,
                normalized_url=validation.normalized_url or publisher_url,
                title=entry_title,
                status=CrawlItemStatus.REJECTED,
                reason=validation.reason,
                metadata={
                    "stage": "publisher_url_validation",
                    "mapped_source_code": mapped_source.code,
                    **metadata,
                },
            )
            return "", None, None

        self._execution_metrics["sources_matched"] += 1

        try:
            with CrawlerHttpClient(mapped_source) as publisher_client:
                publisher_page = publisher_client.get_html(
                    validation.normalized_url
                )
        except RobotsDeniedError as exc:
            self._record_item(
                original_url=entry_url,
                normalized_url=validation.normalized_url,
                title=entry_title,
                status=CrawlItemStatus.FETCH_BLOCKED,
                reason=(
                    "URL penerbit ditemukan, tetapi robots.txt tidak "
                    "mengizinkan pengambilan halaman."
                ),
                error_message=str(exc),
                metadata={
                    "stage": "publisher_robots_denied",
                    "collection_state": "fetch_blocked",
                    "mapped_source_code": mapped_source.code,
                    **metadata,
                },
            )
            return "", None, None
        except CrawlerHttpError as exc:
            self._record_item(
                original_url=entry_url,
                normalized_url=validation.normalized_url,
                title=entry_title,
                status=CrawlItemStatus.FETCH_BLOCKED,
                reason=(
                    "URL penerbit ditemukan, tetapi halaman tidak dapat "
                    "diambil."
                ),
                error_message=str(exc),
                metadata={
                    "stage": "publisher_fetch_blocked",
                    "collection_state": "fetch_blocked",
                    "mapped_source_code": mapped_source.code,
                    **metadata,
                },
            )
            return "", None, None

        final_source = self._registered_source_for_url(
            publisher_page.final_url,
            allowed_sources,
        )
        final_validation = validate_source_url(
            mapped_source,
            publisher_page.final_url,
        )
        if (
            final_source is None
            or final_source.pk != mapped_source.pk
            or not final_validation.is_valid
        ):
            self._record_item(
                original_url=entry_url,
                normalized_url=publisher_page.final_url,
                title=entry_title,
                status=CrawlItemStatus.REJECTED,
                reason=(
                    "Redirect halaman penerbit keluar dari Source atau pola "
                    "URL yang diizinkan."
                ),
                metadata={
                    "stage": "publisher_final_url_validation",
                    "mapped_source_code": mapped_source.code,
                    **metadata,
                },
            )
            return "", None, None

        normalized_page = replace(
            publisher_page,
            final_url=final_validation.normalized_url,
        )
        self._execution_metrics["publisher_pages_fetched"] += 1
        return final_validation.normalized_url, normalized_page, mapped_source

    def _with_google_news_provenance(
        self,
        payload: ArticlePayload,
        *,
        feed_url: str,
        query_batch: GoogleNewsQueryBatch,
        entry_url: str,
        entry_identifier: str,
        source_hint: dict,
        mapped_source: Source,
        resolver_method: str,
    ) -> ArticlePayload:
        return replace(
            payload,
            metadata={
                **payload.metadata,
                "crawler": self.discovery_channel,
                "discovery_channel": self.discovery_channel,
                "retrieval_channel": "publisher_html",
                "google_news": {
                    "provider": "google_news",
                    "discovery_scope": "global_allowed_sources",
                    "query_source": "active_disease_master",
                    "query": query_batch.query,
                    "query_batch": query_batch.index,
                    "query_batch_total": query_batch.total,
                    "term_count": len(query_batch.terms),
                    "max_age_days": self.max_age_days,
                    "feed_url": feed_url,
                    "aggregator_url": entry_url,
                    "entry_identifier": entry_identifier,
                    "source_hint": source_hint,
                    "mapped_source_code": mapped_source.code,
                    "resolver_method": resolver_method,
                },
            },
        )

    def _fetch_query_batch(
        self,
        *,
        query_batch: GoogleNewsQueryBatch,
        discovery_client: CrawlerHttpClient,
    ) -> _GoogleNewsFeedBatch | None:
        feed_url = self.build_feed_url(
            query_text=query_batch.query,
            max_age_days=self.max_age_days,
        )
        self._execution_metrics["feed_batches_requested"] += 1
        self._rss_item_context = {
            "discovery_channel": self.discovery_channel,
            "provider": "google_news",
            "discovery_scope": "global_allowed_sources",
            "query_source": "active_disease_master",
            "query": query_batch.query,
            "query_batch": query_batch.index,
            "query_batch_total": query_batch.total,
            "max_age_days": self.max_age_days,
        }

        try:
            feed_page = discovery_client.get_discovery_feed(
                feed_url,
                allowed_hosts=GOOGLE_NEWS_HOSTS,
                allowed_path_prefixes=GOOGLE_NEWS_FEED_PATHS,
            )
        except (CrawlerHttpError, RobotsDeniedError) as exc:
            self._record_item(
                original_url=feed_url,
                status=CrawlItemStatus.FAILED,
                reason="Feed Google News gagal diambil.",
                error_message=str(exc),
                metadata={"stage": "google_news_feed_download"},
            )
            return

        if not self._is_google_news_url(feed_page.final_url):
            self._record_item(
                original_url=feed_url,
                normalized_url=feed_page.final_url,
                status=CrawlItemStatus.REJECTED,
                reason="Feed Google News dialihkan ke domain yang tidak sah.",
                metadata={"stage": "google_news_feed_redirect"},
            )
            return

        parsed_feed = feedparser.parse(feed_page.text)
        entries = list(parsed_feed.entries)
        if parsed_feed.bozo and not entries:
            self._record_item(
                original_url=feed_url,
                normalized_url=feed_page.final_url,
                status=CrawlItemStatus.FAILED,
                reason="Feed Google News tidak dapat diparsing.",
                error_message=str(parsed_feed.bozo_exception),
                metadata={"stage": "google_news_feed_parsing"},
            )
            return

        self._execution_metrics["feed_batches_succeeded"] += 1
        self._execution_metrics["feed_entries_found"] += len(entries)
        self._execution_metrics["feed_entries_within_age"] += sum(
            1 for entry in entries if not self._is_stale(entry)
        )
        return _GoogleNewsFeedBatch(
            query_batch=query_batch,
            feed_url=feed_url,
            entries=tuple(enumerate(entries, start=1)),
        )

    def _process_entry(
        self,
        *,
        feed_batch: _GoogleNewsFeedBatch,
        index: int,
        entry,
        discovery_client: CrawlerHttpClient,
        allowed_sources: Sequence[Source],
        processed_urls: set[str],
    ) -> ArticlePayload | None:
        query_batch = feed_batch.query_batch
        feed_url = feed_batch.feed_url
        entry_url = self._entry_url(entry)
        entry_title = self._entry_title(entry)
        entry_identifier = self._entry_identifier(entry)
        source_hint = self._entry_source_hint(entry)
        item_metadata = {
            "entry_index": index,
            "entry_identifier": entry_identifier,
            "source_hint": source_hint,
            "feed_url": feed_url,
        }
        self._rss_item_context = {
            "discovery_channel": self.discovery_channel,
            "provider": "google_news",
            "discovery_scope": "global_allowed_sources",
            "query_source": "active_disease_master",
            "query": query_batch.query,
            "query_batch": query_batch.index,
            "query_batch_total": query_batch.total,
            "max_age_days": self.max_age_days,
            **item_metadata,
        }

        if not entry_url:
            self._record_item(
                original_url=f"{feed_url}#entry-{index}",
                title=entry_title,
                status=CrawlItemStatus.METADATA_ONLY,
                reason="Entri Google News tidak memiliki URL.",
                metadata={
                    "stage": "google_news_entry_url",
                    "collection_state": "metadata_only",
                },
            )
            return None

        if self._is_stale(entry):
            self._record_item(
                original_url=entry_url,
                title=entry_title,
                status=CrawlItemStatus.REJECTED,
                reason=(
                    "Entri lebih lama dari batas "
                    f"{self.max_age_days} hari."
                ),
                metadata={"stage": "google_news_entry_age"},
            )
            return None

        publisher_url, resolved_page, mapped_source = (
            self._resolve_publisher_page(
                discovery_client=discovery_client,
                allowed_sources=allowed_sources,
                entry_url=entry_url,
                entry_title=entry_title,
                metadata=item_metadata,
            )
        )
        if not publisher_url or mapped_source is None:
            return None

        resolver_method = str(
            item_metadata.get("resolver_method") or "unknown"
        )
        self._rss_item_context["resolver_method"] = resolver_method

        try:
            normalized_url = normalize_url(publisher_url)
        except ValueError as exc:
            self._record_item(
                original_url=entry_url,
                normalized_url=publisher_url,
                title=entry_title,
                status=CrawlItemStatus.REJECTED,
                reason=str(exc),
                metadata={"stage": "publisher_url_normalization"},
            )
            return None

        if normalized_url in processed_urls:
            self._record_item(
                original_url=entry_url,
                normalized_url=normalized_url,
                title=entry_title,
                status=CrawlItemStatus.DUPLICATE,
                reason=(
                    "URL penerbit ditemukan lebih dari sekali dalam "
                    "proses Google News yang sama."
                ),
                metadata={"stage": "google_news_deduplication"},
            )
            return None
        processed_urls.add(normalized_url)

        if Article.objects.filter(normalized_url=normalized_url).exists():
            self._record_item(
                original_url=entry_url,
                normalized_url=normalized_url,
                title=entry_title,
                status=CrawlItemStatus.DUPLICATE,
                reason=(
                    "Artikel penerbit sudah tersimpan dan dilewati "
                    "tanpa fetch ulang."
                ),
                metadata={"stage": "known_article_skip"},
            )
            return None

        payload = self._parse_article(
            source=mapped_source,
            client=_CachedPublisherClient(resolved_page),
            article_url=normalized_url,
        )
        if payload is None:
            return None

        self._execution_metrics["article_payloads_eligible"] += 1
        return self._with_google_news_provenance(
            payload,
            feed_url=feed_url,
            query_batch=query_batch,
            entry_url=entry_url,
            entry_identifier=entry_identifier,
            source_hint=source_hint,
            mapped_source=mapped_source,
            resolver_method=resolver_method,
        )

    def get_execution_metadata(self) -> dict:
        """Ringkasan funnel untuk audit dan tampilan detail job."""
        return {
            "query_policy": f"when:{self.max_age_days}d",
            "candidate_distribution": "round_robin_query_batches",
            "allowed_source_codes": list(self.allowed_source_codes or ()),
            "funnel": dict(self._execution_metrics),
        }

    def crawl(self) -> Iterable[ArticlePayload]:
        allowed_sources = get_google_news_allowed_sources()
        if self.allowed_source_codes is not None:
            allowed_code_set = set(self.allowed_source_codes)
            allowed_sources = [
                source
                for source in allowed_sources
                if source.code in allowed_code_set
            ]
        if not allowed_sources:
            raise ValueError(
                "Belum ada Source aktif dan terverifikasi yang memiliki "
                "crawler serta pola URL allow."
            )

        disease_scope = build_google_news_disease_scope()
        if not disease_scope.batches:
            raise ValueError(
                "Disease Master belum memiliki penyakit surveilans aktif "
                "yang dapat digunakan untuk pencarian."
            )

        self._candidate_checked = 0
        self._execution_metrics = {
            "feed_batches_requested": 0,
            "feed_batches_succeeded": 0,
            "feed_entries_found": 0,
            "feed_entries_within_age": 0,
            "candidates_checked": 0,
            "publisher_urls_resolved": 0,
            "sources_matched": 0,
            "publisher_pages_fetched": 0,
            "article_payloads_eligible": 0,
        }
        article_limit = self._get_global_article_limit()
        processed_urls: set[str] = set()
        total_yielded = 0

        with CrawlerHttpClient(None) as discovery_client:
            feed_batches = []
            for query_batch in disease_scope.batches:
                feed_batch = self._fetch_query_batch(
                    query_batch=query_batch,
                    discovery_client=discovery_client,
                )
                if feed_batch is not None:
                    feed_batches.append(feed_batch)

            # Satu entri per batch per putaran. Dengan demikian jatah kandidat
            # tidak dapat dihabiskan oleh batch penyakit pertama saja.
            active_batches = [
                (feed_batch, iter(feed_batch.entries))
                for feed_batch in feed_batches
            ]
            candidate_limit = self._get_candidate_limit()

            while (
                active_batches
                and total_yielded < article_limit
                and self._candidate_checked < candidate_limit
            ):
                next_round = []
                for feed_batch, entry_iterator in active_batches:
                    if (
                        total_yielded >= article_limit
                        or self._candidate_checked >= candidate_limit
                    ):
                        break
                    try:
                        index, entry = next(entry_iterator)
                    except StopIteration:
                        continue

                    next_round.append((feed_batch, entry_iterator))
                    self._candidate_checked += 1
                    self._execution_metrics["candidates_checked"] = (
                        self._candidate_checked
                    )
                    payload = self._process_entry(
                        feed_batch=feed_batch,
                        index=index,
                        entry=entry,
                        discovery_client=discovery_client,
                        allowed_sources=allowed_sources,
                        processed_urls=processed_urls,
                    )
                    if payload is None:
                        continue

                    total_yielded += 1
                    yield payload

                active_batches = next_round

        self._rss_item_context = {}
        logger.info(
            "Google News RSS global selesai total_payload=%s checked=%s "
            "allowed_sources=%s max_age_days=%s",
            total_yielded,
            self._candidate_checked,
            len(allowed_sources),
            self.max_age_days,
        )
