import json
import re
import logging

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from datetime import datetime
from django.utils import timezone
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dateutil import parser as date_parser


ARTICLE_JSONLD_TYPES = {
    "Article",
    "NewsArticle",
    "Report",
    "BlogPosting",
    "MedicalScholarlyArticle",
}


@dataclass(frozen=True)
class ArticleLinkCandidate:
    url: str
    anchor_text: str
    context_text: str


@dataclass(frozen=True)
class ParsedArticle:
    title: str
    content: str
    published_at: datetime | None
    author: str
    canonical_url: str
    metadata: dict[str, Any]


def clean_text(
    value: str | None,
) -> str:
    if not value:
        return ""

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def parse_datetime(
    value: str | None,
) -> datetime | None:
    if not value:
        return None

    try:
        parsed = date_parser.parse(
            value
        )
    except (
        ValueError,
        TypeError,
        OverflowError,
    ):
        return None

    if timezone.is_naive(
        parsed
    ):
        parsed = timezone.make_aware(
            parsed,
            timezone.get_current_timezone(),
        )

    return parsed


def extract_json_ld_items(
    soup: BeautifulSoup,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    scripts = soup.find_all(
        "script",
        attrs={
            "type": "application/ld+json",
        },
    )

    for script in scripts:
        raw_text = script.string or script.get_text()

        if not raw_text.strip():
            continue

        try:
            data = json.loads(
                raw_text
            )
        except json.JSONDecodeError:
            continue

        if isinstance(data, dict):
            graph = data.get("@graph")

            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict):
                        items.append(item)

            items.append(data)

        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    items.append(item)

    return items


def json_ld_type_matches(
    item: dict[str, Any],
) -> bool:
    item_type = item.get("@type")

    if isinstance(item_type, str):
        return item_type in ARTICLE_JSONLD_TYPES

    if isinstance(item_type, list):
        return any(
            value in ARTICLE_JSONLD_TYPES
            for value in item_type
        )

    return False


def find_article_json_ld(
    soup: BeautifulSoup,
) -> dict[str, Any]:
    for item in extract_json_ld_items(soup):
        if json_ld_type_matches(item):
            return item

    return {}


def extract_author_from_json_ld(
    value: Any,
) -> str:
    if isinstance(value, str):
        return clean_text(value)

    if isinstance(value, dict):
        return clean_text(
            value.get("name")
        )

    if isinstance(value, list):
        names: list[str] = []

        for item in value:
            name = extract_author_from_json_ld(
                item
            )

            if name:
                names.append(name)

        return ", ".join(names)

    return ""


def extract_title(
    soup: BeautifulSoup,
    json_ld: dict[str, Any],
) -> str:
    candidates: list[str | None] = []

    h1 = soup.find("h1")

    if h1:
        candidates.append(
            h1.get_text(
                " ",
                strip=True,
            )
        )

    meta_selectors = [
        (
            "meta",
            {
                "property": "og:title",
            },
        ),
        (
            "meta",
            {
                "name": "twitter:title",
            },
        ),
        (
            "meta",
            {
                "name": "title",
            },
        ),
    ]

    for tag_name, attrs in meta_selectors:
        tag = soup.find(
            tag_name,
            attrs=attrs,
        )

        if tag:
            candidates.append(
                tag.get("content")
            )

    candidates.extend(
        [
            json_ld.get("headline"),
            json_ld.get("name"),
        ]
    )

    if soup.title:
        candidates.append(
            soup.title.get_text(
                " ",
                strip=True,
            )
        )

    ignored_titles = {
        "generasi sehat, masa depan hebat",
        "kementerian kesehatan republik indonesia",
        "kementerian kesehatan ri",
    }

    for candidate in candidates:
        title = clean_text(candidate)

        if not title:
            continue

        if title.casefold() in ignored_titles:
            continue

        return title

    return ""

def extract_canonical_url(
    soup: BeautifulSoup,
    page_url: str,
    json_ld: dict[str, Any],
) -> str:
    candidate = json_ld.get("url")

    if isinstance(candidate, str) and candidate.strip():
        return urljoin(
            page_url,
            candidate.strip(),
        )

    canonical = soup.find(
        "link",
        attrs={
            "rel": "canonical",
        },
    )

    if canonical and canonical.get("href"):
        return urljoin(
            page_url,
            canonical["href"],
        )

    og_url = soup.find(
        "meta",
        attrs={
            "property": "og:url",
        },
    )

    if og_url and og_url.get("content"):
        return urljoin(
            page_url,
            og_url["content"],
        )

    return page_url


def extract_published_at(
    soup: BeautifulSoup,
    json_ld: dict[str, Any],
) -> datetime | None:
    candidates = [
        json_ld.get("datePublished"),
        json_ld.get("dateCreated"),
    ]

    meta_candidates = [
        (
            "meta",
            {
                "property": "article:published_time",
            },
        ),
        (
            "meta",
            {
                "name": "pubdate",
            },
        ),
        (
            "meta",
            {
                "name": "publish-date",
            },
        ),
        (
            "meta",
            {
                "itemprop": "datePublished",
            },
        ),
    ]

    for tag_name, attrs in meta_candidates:
        tag = soup.find(
            tag_name,
            attrs=attrs,
        )

        if tag:
            candidates.append(
                tag.get("content")
            )

    time_tag = soup.find(
        "time"
    )

    if time_tag:
        candidates.append(
            time_tag.get("datetime")
        )

        candidates.append(
            time_tag.get_text(
                " ",
                strip=True,
            )
        )

    for candidate in candidates:
        parsed = parse_datetime(
            candidate
        )

        if parsed:
            return parsed

    return None


def remove_noise(
    element,
) -> None:
    for selector in (
        "script",
        "style",
        "noscript",
        "iframe",
        "form",
        "button",
        "nav",
        "footer",
        "aside",
        ".advertisement",
        ".ads",
        ".social-share",
        ".related",
        ".recommended",
        ".newsletter",
    ):
        for node in element.select(selector):
            node.decompose()


def extract_content(
    soup: BeautifulSoup,
    json_ld: dict[str, Any],
) -> str:
    article_body = json_ld.get(
        "articleBody"
    )

    if isinstance(article_body, str):
        cleaned_body = clean_text(
            article_body
        )

        if len(cleaned_body) >= 200:
            return cleaned_body

    selectors = [
        "article",
        "[itemprop='articleBody']",
        ".article-body",
        ".article-content",
        ".detail-content",
        ".read__content",
        ".post-content",
        ".entry-content",
        ".content-detail",
        "main",
    ]

    best_text = ""

    for selector in selectors:
        for element in soup.select(selector):
            remove_noise(element)

            paragraphs = [
                clean_text(
                    paragraph.get_text(
                        " ",
                        strip=True,
                    )
                )
                for paragraph in element.find_all(
                    [
                        "p",
                        "h2",
                        "h3",
                        "li",
                    ]
                )
            ]

            paragraphs = [
                paragraph
                for paragraph in paragraphs
                if len(paragraph) >= 20
            ]

            text = "\n\n".join(
                paragraphs
            )

            if len(text) > len(best_text):
                best_text = text

    return best_text.strip()


def parse_article_html(
    *,
    html: str,
    page_url: str,
) -> ParsedArticle:
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    logger.info(
        (
            "Parser artikel url=%s "
            "html_length=%s "
            "paragraph_count=%s "
            "article_count=%s"
        ),
        page_url,
        len(html),
        len(soup.find_all("p")),
        len(soup.find_all("article")),
    )

    json_ld = find_article_json_ld(
        soup
    )

    title = extract_title(
        soup,
        json_ld,
    )

    content = extract_content(
        soup,
        json_ld,
    )

    if len(content) < 100:
        fallback_content = (
            extract_fallback_paragraphs(
                soup
            )
        )

        logger.info(
            (
                "Fallback paragraf digunakan "
                "url=%s content_length=%s"
            ),
            page_url,
            len(fallback_content),
        )

        if len(fallback_content) >= 100:
            content = fallback_content

    published_at = extract_published_at(
        soup,
        json_ld,
    )

    author = extract_author_from_json_ld(
        json_ld.get("author")
    )

    canonical_url = extract_canonical_url(
        soup,
        page_url,
        json_ld,
    )

    metadata = {
        "parser": "generic_html",
        "json_ld_type": json_ld.get("@type"),
        "author": author,
    }

    return ParsedArticle(
        title=title,
        content=content,
        published_at=published_at,
        author=author,
        canonical_url=canonical_url,
        metadata=metadata,
    )


def extract_fallback_paragraphs(
    soup: BeautifulSoup,
) -> str:
    ignored_parent_tags = {
        "nav",
        "header",
        "footer",
        "aside",
        "form",
    }

    paragraphs: list[str] = []
    seen: set[str] = set()

    for paragraph in soup.find_all("p"):
        inside_ignored_element = any(
            getattr(parent, "name", None)
            in ignored_parent_tags
            for parent in paragraph.parents
        )

        if inside_ignored_element:
            continue

        text = paragraph.get_text(
            " ",
            strip=True,
        )

        normalized_text = " ".join(
            text.split()
        )

        if len(normalized_text) < 30:
            continue

        if normalized_text in seen:
            continue

        seen.add(normalized_text)
        paragraphs.append(
            normalized_text
        )

    return "\n\n".join(
        paragraphs
    ).strip()


def _extract_anchor_context(
    anchor,
) -> str:
    """
    Mengambil konteks kartu berita tanpa memakai container besar.

    Prioritas:
    1. elemen article terdekat;
    2. elemen li terdekat;
    3. parent langsung bila ukurannya masih kecil.
    """
    parts: list[str] = []

    anchor_text = clean_text(
        anchor.get_text(
            " ",
            strip=True,
        )
    )

    if anchor_text:
        parts.append(anchor_text)

    title_attribute = clean_text(
        anchor.get("title")
    )

    if (
        title_attribute
        and title_attribute not in parts
    ):
        parts.append(title_attribute)

    image = anchor.find("img")

    if image:
        for attribute in ("alt", "title"):
            image_text = clean_text(
                image.get(attribute)
            )

            if (
                image_text
                and image_text not in parts
            ):
                parts.append(image_text)

    card = (
        anchor.find_parent("article")
        or anchor.find_parent("li")
    )

    if card is None:
        direct_parent = anchor.parent

        if direct_parent is not None:
            parent_text = clean_text(
                direct_parent.get_text(
                    " ",
                    strip=True,
                )
            )
            parent_link_count = len(
                direct_parent.find_all(
                    "a",
                    href=True,
                )
            )

            if (
                len(parent_text) <= 1200
                and parent_link_count <= 8
            ):
                card = direct_parent

    if card is not None:
        for selector in (
            "h1",
            "h2",
            "h3",
            "h4",
            ".title",
            ".headline",
            ".summary",
            ".description",
            ".excerpt",
            "p",
        ):
            node = card.select_one(selector)

            if not node:
                continue

            text = clean_text(
                node.get_text(
                    " ",
                    strip=True,
                )
            )

            if (
                text
                and text not in parts
            ):
                parts.append(text)

            if len(" ".join(parts)) >= 700:
                break

    return clean_text(
        " ".join(parts)
    )[:1000]


def discover_article_links(
    *,
    html: str,
    page_url: str,
) -> list[ArticleLinkCandidate]:
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    discovered: list[ArticleLinkCandidate] = []
    seen: set[str] = set()

    for anchor in soup.find_all(
        "a",
        href=True,
    ):
        href = anchor.get(
            "href",
            "",
        ).strip()

        if not href:
            continue

        if href.startswith(
            (
                "#",
                "mailto:",
                "tel:",
                "javascript:",
            )
        ):
            continue

        absolute_url = urljoin(
            page_url,
            href,
        )

        if absolute_url in seen:
            continue

        seen.add(
            absolute_url
        )

        discovered.append(
            ArticleLinkCandidate(
                url=absolute_url,
                anchor_text=clean_text(
                    anchor.get_text(
                        " ",
                        strip=True,
                    )
                ),
                context_text=_extract_anchor_context(
                    anchor
                ),
            )
        )

    return discovered
