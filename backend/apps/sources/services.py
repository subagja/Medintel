import re
from dataclasses import dataclass
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)

from .models import (
    Source,
    SourceUrlPattern,
)


TRACKING_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "dclid",
    "mc_cid",
    "mc_eid",
}


@dataclass(frozen=True)
class UrlValidationResult:
    is_valid: bool
    normalized_url: str
    reason: str = ""
    matched_pattern_id: str | None = None


@dataclass(frozen=True)
class SourceCrawlReadiness:
    is_ready: bool
    errors: tuple[str, ...]


def normalize_domain(
    domain: str,
) -> str:
    normalized = domain.strip().lower()

    if normalized.startswith(
        (
            "http://",
            "https://",
        )
    ):
        normalized = urlsplit(
            normalized
        ).netloc

    normalized = normalized.split(":")[0]
    normalized = normalized.removeprefix(
        "www."
    )

    return normalized.rstrip(".")


def normalize_url(
    url: str,
) -> str:
    raw_url = url.strip()

    if not raw_url:
        raise ValueError(
            "URL tidak boleh kosong."
        )

    parts = urlsplit(
        raw_url
    )

    if parts.scheme.lower() not in {
        "http",
        "https",
    }:
        raise ValueError(
            "URL harus menggunakan protokol "
            "http atau https."
        )

    hostname = parts.hostname

    if not hostname:
        raise ValueError(
            "URL tidak memiliki domain yang valid."
        )

    scheme = parts.scheme.lower()

    normalized_hostname = normalize_domain(
        hostname
    )

    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(
            "Port pada URL tidak valid."
        ) from exc

    if port and not (
        (
            scheme == "http"
            and port == 80
        )
        or (
            scheme == "https"
            and port == 443
        )
    ):
        netloc = (
            f"{normalized_hostname}:{port}"
        )
    else:
        netloc = normalized_hostname

    path = re.sub(
        r"/{2,}",
        "/",
        parts.path or "/",
    )

    if path != "/":
        path = path.rstrip("/")

    filtered_query = [
        (
            key,
            value,
        )
        for key, value in parse_qsl(
            parts.query,
            keep_blank_values=True,
        )
        if key.lower()
        not in TRACKING_QUERY_PARAMS
    ]

    filtered_query.sort()

    query = urlencode(
        filtered_query,
        doseq=True,
    )

    return urlunsplit(
        (
            scheme,
            netloc,
            path,
            query,
            "",
        )
    )


def is_domain_allowed(
    source: Source,
    url: str,
) -> bool:
    hostname = urlsplit(
        url
    ).hostname

    if not hostname:
        return False

    article_domain = normalize_domain(
        hostname
    )

    source_domain = normalize_domain(
        source.domain
    )

    if article_domain == source_domain:
        return True

    return (
        source.allow_subdomains
        and article_domain.endswith(
            f".{source_domain}"
        )
    )


def pattern_matches(
    pattern: SourceUrlPattern,
    normalized_url: str,
) -> bool:
    parsed = urlsplit(
        normalized_url
    )

    path = parsed.path or "/"

    path_with_query = path

    if parsed.query:
        path_with_query = (
            f"{path}?{parsed.query}"
        )

    candidates = (
        normalized_url,
        path,
        path_with_query,
    )

    pattern_value = (
        pattern.pattern.strip()
    )

    if (
        pattern.match_type
        == SourceUrlPattern.MatchType.PREFIX
    ):
        lowered_pattern = (
            pattern_value.lower()
        )

        return any(
            candidate.lower().startswith(
                lowered_pattern
            )
            for candidate in candidates
        )

    if (
        pattern.match_type
        == SourceUrlPattern.MatchType.CONTAINS
    ):
        lowered_pattern = (
            pattern_value.lower()
        )

        return any(
            lowered_pattern
            in candidate.lower()
            for candidate in candidates
        )

    if (
        pattern.match_type
        == SourceUrlPattern.MatchType.REGEX
    ):
        try:
            return any(
                re.search(
                    pattern_value,
                    candidate,
                    flags=re.IGNORECASE,
                )
                is not None
                for candidate in candidates
            )
        except re.error:
            return False

    return False


def validate_source_url(
    source: Source,
    url: str,
    *,
    require_allow_pattern: bool = True,
    require_source_ready: bool = True,
) -> UrlValidationResult:
    try:
        normalized_url = normalize_url(
            url
        )
    except ValueError as exc:
        return UrlValidationResult(
            is_valid=False,
            normalized_url="",
            reason=str(exc),
        )

    if require_source_ready:
        if not source.is_active:
            return UrlValidationResult(
                is_valid=False,
                normalized_url=normalized_url,
                reason="Sumber tidak aktif.",
            )

        if not source.is_verified:
            return UrlValidationResult(
                is_valid=False,
                normalized_url=normalized_url,
                reason=(
                    "Sumber belum tervalidasi."
                ),
            )

    if not is_domain_allowed(
        source,
        normalized_url,
    ):
        return UrlValidationResult(
            is_valid=False,
            normalized_url=normalized_url,
            reason=(
                "Domain URL tidak sesuai "
                "dengan sumber."
            ),
        )

    patterns = (
        source.url_patterns.filter(
            is_active=True,
        )
        .order_by(
            "priority",
            "id",
        )
    )

    deny_patterns = patterns.filter(
        pattern_type=(
            SourceUrlPattern.PatternType.DENY
        ),
    )

    for pattern in deny_patterns:
        if pattern_matches(
            pattern,
            normalized_url,
        ):
            return UrlValidationResult(
                is_valid=False,
                normalized_url=normalized_url,
                reason=(
                    "URL cocok dengan pola "
                    f"penolakan: {pattern.pattern}"
                ),
                matched_pattern_id=str(
                    pattern.id
                ),
            )

    allow_patterns = patterns.filter(
        pattern_type=(
            SourceUrlPattern.PatternType.ALLOW
        ),
    )

    if not allow_patterns.exists():
        if require_allow_pattern:
            return UrlValidationResult(
                is_valid=False,
                normalized_url=normalized_url,
                reason=(
                    "Sumber belum memiliki "
                    "pola URL yang diizinkan."
                ),
            )

        return UrlValidationResult(
            is_valid=True,
            normalized_url=normalized_url,
            reason="URL valid.",
        )

    for pattern in allow_patterns:
        if pattern_matches(
            pattern,
            normalized_url,
        ):
            return UrlValidationResult(
                is_valid=True,
                normalized_url=normalized_url,
                reason="URL valid.",
                matched_pattern_id=str(
                    pattern.id
                ),
            )

    return UrlValidationResult(
        is_valid=False,
        normalized_url=normalized_url,
        reason=(
            "URL tidak cocok dengan pola "
            "yang diizinkan."
        ),
    )


def check_source_crawl_readiness(
    source: Source,
) -> SourceCrawlReadiness:
    errors: list[str] = []

    if not source.is_active:
        errors.append(
            "Sumber tidak aktif."
        )

    if not source.is_verified:
        errors.append(
            "Sumber belum diverifikasi."
        )

    if not source.crawl_enabled:
        errors.append(
            "Crawling belum diaktifkan."
        )

    if (
        source.crawl_strategy
        != Source.CrawlStrategy.MANUAL
        and not source.seed_urls.filter(
            is_active=True,
        ).exists()
    ):
        errors.append(
            "Sumber belum memiliki "
            "URL awal aktif."
        )

    if (
        source.crawl_strategy
        != Source.CrawlStrategy.MANUAL
        and not source.url_patterns.filter(
            is_active=True,
            pattern_type=(
                SourceUrlPattern.PatternType.ALLOW
            ),
        ).exists()
    ):
        errors.append(
            "Sumber belum memiliki "
            "aturan URL allow."
        )

    if source.max_articles_per_run < 1:
        errors.append(
            "Jumlah artikel per proses "
            "harus lebih dari nol."
        )

    if source.request_delay_seconds < 0:
        errors.append(
            "Jeda permintaan tidak boleh negatif."
        )

    if source.request_timeout_seconds < 1:
        errors.append(
            "Timeout permintaan minimal "
            "adalah 1 detik."
        )

    return SourceCrawlReadiness(
        is_ready=not errors,
        errors=tuple(errors),
    )