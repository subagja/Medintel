import re
from dataclasses import dataclass
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)

from .models import Source, SourceUrlPattern


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


def normalize_domain(domain: str) -> str:
    normalized = domain.strip().lower()

    if normalized.startswith("http://") or normalized.startswith("https://"):
        normalized = urlsplit(normalized).netloc

    normalized = normalized.split(":")[0]
    normalized = normalized.removeprefix("www.")

    return normalized.rstrip(".")


def normalize_url(url: str) -> str:
    raw_url = url.strip()

    if not raw_url:
        raise ValueError("URL tidak boleh kosong.")

    parts = urlsplit(raw_url)

    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("URL harus menggunakan protokol http atau https.")

    hostname = parts.hostname

    if not hostname:
        raise ValueError("URL tidak memiliki domain yang valid.")

    scheme = parts.scheme.lower()
    hostname = normalize_domain(hostname)

    port = parts.port
    if port and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = re.sub(r"/{2,}", "/", parts.path or "/")

    if path != "/":
        path = path.rstrip("/")

    filtered_query = [
        (key, value)
        for key, value in parse_qsl(
            parts.query,
            keep_blank_values=True,
        )
        if key.lower() not in TRACKING_QUERY_PARAMS
    ]

    filtered_query.sort()

    query = urlencode(filtered_query, doseq=True)

    return urlunsplit(
        (
            scheme,
            netloc,
            path,
            query,
            "",
        )
    )


def is_domain_allowed(source: Source, url: str) -> bool:
    hostname = urlsplit(url).hostname

    if not hostname:
        return False

    article_domain = normalize_domain(hostname)
    source_domain = normalize_domain(source.domain)

    return (
        article_domain == source_domain
        or article_domain.endswith(f".{source_domain}")
    )


def pattern_matches(
    pattern: SourceUrlPattern,
    normalized_url: str,
) -> bool:
    if pattern.is_regex:
        try:
            return bool(
                re.search(
                    pattern.pattern,
                    normalized_url,
                    flags=re.IGNORECASE,
                )
            )
        except re.error:
            return False

    return pattern.pattern.lower() in normalized_url.lower()


def validate_source_url(
    source: Source,
    url: str,
) -> UrlValidationResult:
    try:
        normalized_url = normalize_url(url)
    except ValueError as exc:
        return UrlValidationResult(
            is_valid=False,
            normalized_url="",
            reason=str(exc),
        )

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
            reason="Sumber belum tervalidasi.",
        )

    if not is_domain_allowed(source, normalized_url):
        return UrlValidationResult(
            is_valid=False,
            normalized_url=normalized_url,
            reason="Domain URL tidak sesuai dengan sumber.",
        )

    patterns = source.url_patterns.filter(
        is_active=True,
    )

    deny_patterns = patterns.filter(
        pattern_type=SourceUrlPattern.PatternType.DENY,
    )

    for pattern in deny_patterns:
        if pattern_matches(pattern, normalized_url):
            return UrlValidationResult(
                is_valid=False,
                normalized_url=normalized_url,
                reason=f"URL cocok dengan pola penolakan: {pattern.pattern}",
            )

    allow_patterns = patterns.filter(
        pattern_type=SourceUrlPattern.PatternType.ALLOW,
    )

    if allow_patterns.exists():
        is_allowed = any(
            pattern_matches(pattern, normalized_url)
            for pattern in allow_patterns
        )

        if not is_allowed:
            return UrlValidationResult(
                is_valid=False,
                normalized_url=normalized_url,
                reason="URL tidak cocok dengan pola yang diizinkan.",
            )

    return UrlValidationResult(
        is_valid=True,
        normalized_url=normalized_url,
        reason="URL valid.",
    )