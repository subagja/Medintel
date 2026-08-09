"""Audit aman sebelum mengaktifkan kanal RSS resmi."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup

from apps.crawlers.http_client import (
    CrawlerHttpClient,
    CrawlerHttpError,
    RobotsDeniedError,
)

from .models import Source, SourceSeedUrl, SourceUrlPattern
from .rss_registry import OFFICIAL_RSS_CANDIDATES
from .services import is_domain_allowed, validate_source_url


RSS_CONTENT_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
}


@dataclass(frozen=True)
class FeedAuditResult:
    requested_url: str
    final_url: str = ""
    is_valid: bool = False
    reason: str = ""
    total_entries: int = 0
    checked_entries: int = 0
    accepted_entries: int = 0

    @property
    def acceptance_ratio(self) -> float:
        if not self.checked_entries:
            return 0.0
        return self.accepted_entries / self.checked_entries


@dataclass(frozen=True)
class CandidateCollection:
    urls: tuple[str, ...]
    discovery_error: str = ""


def source_activation_blockers(source: Source) -> tuple[str, ...]:
    """Syarat yang tidak boleh diubah diam-diam oleh aktivator RSS."""
    blockers: list[str] = []

    if not source.is_active:
        blockers.append("Sumber tidak aktif.")
    if not source.is_verified:
        blockers.append("Sumber belum diverifikasi.")
    if not source.url_patterns.filter(
        is_active=True,
        pattern_type=SourceUrlPattern.PatternType.ALLOW,
    ).exists():
        blockers.append("Sumber belum memiliki aturan URL allow aktif.")

    return tuple(blockers)


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


def discover_feed_urls(
    source: Source,
    client: CrawlerHttpClient,
) -> tuple[str, ...]:
    """Temukan link RSS/Atom yang diumumkan pada HTML resmi Source."""
    page = client.get_html(source.base_url)

    if not is_domain_allowed(source, page.final_url):
        return ()

    soup = BeautifulSoup(page.text, "html.parser")
    discovered: list[str] = []

    for node in soup.find_all("link", href=True):
        rel_values = {
            str(value).casefold()
            for value in (node.get("rel") or ())
        }
        content_type = str(node.get("type") or "").casefold().strip()

        if "alternate" not in rel_values:
            continue
        if not any(
            content_type.startswith(candidate)
            for candidate in RSS_CONTENT_TYPES
        ):
            continue

        candidate_url = urljoin(page.final_url, str(node["href"]).strip())

        if is_domain_allowed(source, candidate_url):
            discovered.append(candidate_url)

    return tuple(dict.fromkeys(discovered))


def collect_feed_candidates(
    source: Source,
    client: CrawlerHttpClient,
    *,
    manual_urls: tuple[str, ...] = (),
    discover_from_html: bool = True,
) -> CandidateCollection:
    urls: list[str] = list(manual_urls)
    urls.extend(
        source.seed_urls.filter(
            seed_type=SourceSeedUrl.SeedType.RSS,
        ).values_list("url", flat=True)
    )
    urls.extend(OFFICIAL_RSS_CANDIDATES.get(source.code, ()))

    discovery_error = ""

    if discover_from_html:
        try:
            urls.extend(discover_feed_urls(source, client))
        except (CrawlerHttpError, RobotsDeniedError) as exc:
            discovery_error = str(exc)

    return CandidateCollection(
        urls=tuple(dict.fromkeys(url for url in urls if url)),
        discovery_error=discovery_error,
    )


def audit_feed_candidate(
    source: Source,
    client: CrawlerHttpClient,
    feed_url: str,
    *,
    sample_size: int = 12,
    minimum_acceptance_ratio: float = 0.5,
) -> FeedAuditResult:
    """Pastikan feed valid dan mayoritas link contoh lolos policy Source."""
    if not is_domain_allowed(source, feed_url):
        return FeedAuditResult(
            requested_url=feed_url,
            reason="Domain URL feed tidak sesuai dengan Source.",
        )

    try:
        page = client.get_feed(feed_url)
    except (CrawlerHttpError, RobotsDeniedError) as exc:
        return FeedAuditResult(
            requested_url=feed_url,
            reason=str(exc),
        )

    if not is_domain_allowed(source, page.final_url):
        return FeedAuditResult(
            requested_url=feed_url,
            final_url=page.final_url,
            reason="Redirect feed keluar dari domain Source.",
        )

    parsed = feedparser.parse(page.text)
    entries = list(parsed.entries)

    if parsed.bozo and not entries:
        return FeedAuditResult(
            requested_url=feed_url,
            final_url=page.final_url,
            reason=f"RSS/Atom tidak dapat diparsing: {parsed.bozo_exception}",
        )

    if not entries:
        return FeedAuditResult(
            requested_url=feed_url,
            final_url=page.final_url,
            reason="Feed valid tetapi tidak memiliki entri artikel.",
        )

    entry_urls = [
        entry_url
        for entry in entries
        if (entry_url := _entry_url(entry))
    ][: max(1, sample_size)]

    if not entry_urls:
        return FeedAuditResult(
            requested_url=feed_url,
            final_url=page.final_url,
            total_entries=len(entries),
            reason="Entri feed tidak memiliki URL artikel.",
        )

    accepted = sum(
        1
        for entry_url in entry_urls
        if validate_source_url(source, entry_url).is_valid
    )
    checked = len(entry_urls)
    ratio = accepted / checked

    if accepted < 1:
        reason = (
            "Tidak ada URL contoh yang lolos domain serta pola allow/deny "
            "Source."
        )
    elif ratio < minimum_acceptance_ratio:
        reason = (
            "Proporsi URL artikel yang lolos policy Source terlalu rendah "
            f"({accepted}/{checked})."
        )
    else:
        reason = "Feed dan URL artikel sesuai policy Source."

    return FeedAuditResult(
        requested_url=feed_url,
        final_url=page.final_url,
        is_valid=accepted >= 1 and ratio >= minimum_acceptance_ratio,
        reason=reason,
        total_entries=len(entries),
        checked_entries=checked,
        accepted_entries=accepted,
    )
