import json
import re
from difflib import SequenceMatcher
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from intel.models import PublisherDomainAlias
from intel.services.google_news_resolver import resolve_google_news_url


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)

REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}

BLOCKED_DOMAINS = [
    "google.com",
    "news.google.com",
    "gstatic.com",
    "googleusercontent.com",
    "accounts.google.com",
    "policies.google.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "youtube.com",
    "tiktok.com",
]

NON_ARTICLE_PATTERNS = [
    "/tentang-kami",
    "/tentang_kami",
    "/tentang",
    "/about",
    "/about-us",
    "/redaksi",
    "/kontak",
    "/contact",
    "/privacy",
    "/privacy-policy",
    "/terms",
    "/disclaimer",
    "/pedoman",
    "/sitemap",
    "/rss",
    "/tag/",
    "/kategori/",
    "/category/",
    "/author/",
    "/login",
    "/register",
    "/search",
    "/cari",
]

ARTICLE_PATH_HINTS = [
    "/berita/",
    "/news/",
    "/daerah/",
    "/kesehatan/",
    "/regional/",
    "/read/",
    "/nasional/",
    "/artikel/",
    "/siaran/",
    "/features/",
]

DISCOVERY_PATHS = [
    "/",
    "/berita",
    "/berita/",
    "/news",
    "/news/",
    "/artikel",
    "/artikel/",
    "/siaran-pers",
    "/siaran-pers/",
    "/informasi",
    "/informasi/",
    "/kategori/berita",
    "/category/berita",
    "/regional",
    "/regional/",
]

DOMAIN_DISCOVERY_PATHS = {
    "babelprov.go.id": [
        "/",
        "/berita",
        "/berita/",
        "/siaran-pers",
        "/siaran-pers/",
        "/informasi",
        "/informasi/",
    ],
    "rri.co.id": [
        "/",
        "/regional",
        "/regional/",
        "/search?keyword={query}",
    ],
    "kalimantanpost.com": [
        "/",
        "/category/berita/",
        "/category/daerah/",
        "/category/kesehatan/",
    ],
}

# =========================================================
# BASIC TEXT HELPERS
# =========================================================

def clean_text(value: str) -> str:
    if not value:
        return ""

    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def clean_title_for_search(title: str) -> str:
    """
    Google News title biasanya:
    'Judul Artikel - Nama Media'

    Untuk search/matching, nama media di belakang dibuang.
    """
    title = clean_text(title)

    if " - " in title:
        title = title.rsplit(" - ", 1)[0].strip()

    return title


def extract_source_from_title(title: str) -> str:
    """
    Ambil nama media dari judul Google News.
    Contoh:
    'Riau Masuk Zona Merah Polio ... - Sabang Merauke NEWS'
    -> 'Sabang Merauke NEWS'
    """
    title = clean_text(title)

    if " - " not in title:
        return ""

    return title.rsplit(" - ", 1)[-1].strip()


def is_fallback_source_text(text: str, title: str = "", trusted_url: bool = False) -> bool:
    """
    Deteksi apakah teks hanya berasal dari judul/RSS, bukan artikel penuh.

    Jika trusted_url=True, teks pendek dari URL manual tetap boleh dipakai
    selama panjangnya cukup untuk membuat ringkasan dasar.
    """
    text = clean_text(text)
    title_clean = clean_title_for_search(title)

    if not text:
        return True

    if trusted_url:
        # Untuk URL manual, jangan terlalu ketat.
        # Banyak situs hanya bisa diambil meta/title/description, tapi tetap berguna.
        if len(text) >= 250:
            return False

    if len(text) < 500:
        return True

    if title_clean and text.lower().count(title_clean.lower()[:50]) >= 1 and len(text) < 800:
        return True

    return False

def split_sentences(text: str) -> list[str]:
    text = clean_text(text)

    if not text:
        return []

    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if len(p.strip()) > 25]


def title_similarity(a: str, b: str) -> float:
    a = clean_title_for_search(a).lower()
    b = clean_title_for_search(b).lower()

    if not a or not b:
        return 0.0

    return SequenceMatcher(None, a, b).ratio()


def slugify_title(title: str) -> str:
    title = clean_title_for_search(title).lower()
    title = title.replace(".", "")
    title = re.sub(r"[^a-z0-9\s-]", " ", title)
    title = re.sub(r"[\s-]+", "-", title)
    return title.strip("-")


def title_words_for_match(title: str) -> list[str]:
    title_clean = clean_title_for_search(title).lower()
    return [
        w for w in re.findall(r"[a-zA-Z0-9]+", title_clean)
        if len(w) >= 4
    ]


def is_blocked_domain(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return True

    return any(domain in host for domain in BLOCKED_DOMAINS)


def is_probably_non_article_url(url: str) -> bool:
    if not url:
        return True

    try:
        parsed = urlparse(url)
        path = parsed.path.lower()
        query = parsed.query.lower()
    except Exception:
        return True

    if any(pattern in path for pattern in NON_ARTICLE_PATTERNS):
        return True

    # Tolak halaman search berbasis query parameter.
    # Contoh:
    # /search?keyword=...
    # /?s=...
    # /cari?q=...
    if any(x in query for x in ["keyword=", "q=", "s=", "search="]):
        if any(x in path for x in ["/search", "/cari"]) or path in ["", "/"]:
            return True

    return False


def is_article_relevant_to_title(
    text: str,
    title: str,
    metadata: dict | None = None,
    url: str = "",
) -> bool:
    text = clean_text(text).lower()
    clean_title = clean_title_for_search(title).lower()
    metadata = metadata or {}

    if not text or not clean_title:
        return False

    if is_probably_non_article_url(url):
        return False

    meta_title = clean_text(metadata.get("title", "")).lower()
    meta_desc = clean_text(metadata.get("description", "")).lower()
    combined_head = f"{meta_title} {meta_desc}".strip()

    title_words = title_words_for_match(clean_title)

    if not title_words:
        return False

    matched_in_text = sum(1 for w in title_words if w in text)
    matched_in_head = sum(1 for w in title_words if w in combined_head)

    ratio_text = matched_in_text / len(title_words)
    ratio_head = matched_in_head / len(title_words)

    # Kalau sebagian besar kata judul muncul di meta/title atau body, anggap relevan.
    if ratio_head >= 0.45:
        return True

    if ratio_text >= 0.40:
        return True

    # Khusus judul panjang, minimal 4 kata penting muncul.
    if matched_in_text >= 4:
        return True

    return False


def is_text_too_weak(text: str, title: str = "") -> bool:
    text = clean_text(text)
    title = clean_title_for_search(title)

    if len(text) < 500:
        return True

    if title and text.lower().count(title.lower()[:60]) >= 2 and len(text) < 1000:
        return True

    weak_markers = [
        "google news",
        "comprehensive, up-to-date news coverage",
        "aggregated from sources",
        "aktifkan javascript",
        "enable javascript",
        "browser anda",
        "subscribe",
        "sign in",
        "masuk untuk melanjutkan",
        "access denied",
        "forbidden",
    ]

    low = text.lower()

    if any(marker in low for marker in weak_markers) and len(text) < 1500:
        return True

    return False


# =========================================================
# HTML / ARTICLE EXTRACTION
# =========================================================

def get_meta_content(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})

        if tag and tag.get("content"):
            return clean_text(tag.get("content"))

    return ""


def get_jsonld_article_body(soup: BeautifulSoup) -> str:
    bodies = []

    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()

        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            continue

        queue = data if isinstance(data, list) else [data]

        while queue:
            item = queue.pop(0)

            if not isinstance(item, dict):
                continue

            graph = item.get("@graph")

            if isinstance(graph, list):
                queue.extend([x for x in graph if isinstance(x, dict)])

            article_body = item.get("articleBody")
            if article_body:
                bodies.append(clean_text(article_body))

            description = item.get("description")
            if description:
                bodies.append(clean_text(description))

            headline = item.get("headline")
            if headline:
                bodies.append(clean_text(headline))

    return clean_text(" ".join(bodies))


def extract_article_text_from_html(html: str) -> tuple[str, dict]:
    soup = BeautifulSoup(html or "", "html.parser")

    metadata = {
        "title": get_meta_content(soup, "og:title", "twitter:title"),
        "description": get_meta_content(soup, "og:description", "description", "twitter:description"),
        "canonical_url": "",
    }

    canonical = soup.find("link", rel=lambda x: x and "canonical" in x)

    if canonical and canonical.get("href"):
        metadata["canonical_url"] = canonical.get("href")

    og_url = get_meta_content(soup, "og:url")

    if og_url:
        metadata["canonical_url"] = og_url

    jsonld_text = get_jsonld_article_body(soup)

    for tag in soup([
        "script",
        "style",
        "noscript",
        "iframe",
        "svg",
        "form",
        "header",
        "footer",
        "nav",
        "aside",
    ]):
        tag.decompose()

    article = soup.find("article")
    scopes = []

    if article:
        scopes.append(article)

    for selector in [
        {"class_": re.compile(r"(article|content|detail|post|entry|read|news|body|story)", re.I)},
        {"id": re.compile(r"(article|content|detail|post|entry|read|news|body|story)", re.I)},
    ]:
        found = soup.find_all(["div", "main", "section"], **selector)
        scopes.extend(found[:6])

    if not scopes:
        scopes = [soup]

    paragraphs = []

    for scope in scopes:
        for p in scope.find_all(["p", "li"]):
            txt = clean_text(p.get_text(" ", strip=True))

            if len(txt) < 35:
                continue

            low = txt.lower()

            if any(x in low for x in [
                "baca juga",
                "ikuti kami",
                "download aplikasi",
                "copyright",
                "redaksi",
                "advertisement",
                "iklan",
                "login",
                "subscribe",
                "bagikan artikel",
                "komentar",
                "tag:",
            ]):
                continue

            paragraphs.append(txt)

    deduped = []
    seen = set()

    for paragraph in paragraphs:
        key = paragraph.lower()[:140]

        if key in seen:
            continue

        seen.add(key)
        deduped.append(paragraph)

    combined = clean_text(" ".join([
        metadata.get("title", ""),
        metadata.get("description", ""),
        jsonld_text,
        " ".join(deduped),
    ]))

    return combined[:18000], metadata


def request_html(url: str, timeout: int = 10) -> tuple[str, str, str]:
    if not url:
        return "", "", "URL sumber kosong."

    parsed = urlparse(url)

    if parsed.scheme not in ["http", "https"]:
        return "", "", "URL sumber tidak valid."

    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers=REQUEST_HEADERS,
            allow_redirects=True,
        )

        if response.status_code >= 400:
            return "", response.url or url, f"HTTP {response.status_code}"

        return response.text or "", response.url or url, ""

    except requests.exceptions.Timeout:
        return "", url, "Timeout saat mengambil artikel."
    except requests.exceptions.RequestException as exc:
        return "", url, f"Gagal mengambil artikel: {exc}"


def find_external_article_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    links = []

    canonical = soup.find("link", rel=lambda x: x and "canonical" in x)

    if canonical and canonical.get("href"):
        links.append((3, urljoin(base_url, canonical.get("href"))))

    og_url = get_meta_content(soup, "og:url")

    if og_url:
        links.append((3, urljoin(base_url, og_url)))

    for a in soup.find_all("a", href=True):
        href = a.get("href")
        url = urljoin(base_url, href)

        if not url.startswith(("http://", "https://")):
            continue

        if is_blocked_domain(url):
            continue

        if is_probably_non_article_url(url):
            continue

        text = clean_text(a.get_text(" ", strip=True)).lower()
        score = 0

        if any(k in text for k in ["baca", "selengkapnya", "artikel", "source", "sumber"]):
            score += 3

        if len(text) > 20:
            score += 1

        if any(hint in url.lower() for hint in ARTICLE_PATH_HINTS):
            score += 2

        links.append((score, url))

    links.sort(key=lambda x: x[0], reverse=True)

    result = []
    seen = set()

    for _, url in links:
        if url in seen:
            continue

        seen.add(url)
        result.append(url)

    return result[:5]


# =========================================================
# PUBLISHER SEARCH FALLBACK
# =========================================================

def normalize_source_alias(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^a-z0-9\s\.\-]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value

def source_name_to_domain_candidates(source_name: str, title: str = "") -> list[str]:
    primary = source_name_to_domain(source_name, title=title)

    candidates = []

    if primary:
        candidates.append(primary)

    source_name_clean = clean_text(source_name)
    if not source_name_clean or source_name_clean.lower() in ["google news", "google", "google rss"]:
        source_name_clean = extract_source_from_title(title)

    normalized = normalize_source_alias(source_name_clean)

    db_aliases = PublisherDomainAlias.objects.filter(is_active=True)

    for item in db_aliases:
        alias_norm = item.normalized_alias or normalize_source_alias(item.alias)

        if alias_norm and (
            alias_norm in normalized
            or normalized in alias_norm
            or item.domain.lower() in normalized
        ):
            candidates.append(item.domain.strip().lower())

    result = []
    seen = set()

    for domain in candidates:
        domain = domain.lower().strip()
        if not domain or domain in seen:
            continue

        seen.add(domain)
        result.append(domain)

    return result

def source_name_to_domain(source_name: str, title: str = "") -> str:
    """
    Resolve domain publisher secara dinamis dari database PublisherDomainAlias.
    Fallback terakhir hanya mencoba membaca domain langsung dari source_name/title.
    """
    source_name = clean_text(source_name)

    if not source_name or source_name.lower() in ["google news", "google", "google rss"]:
        source_name = extract_source_from_title(title)

    if not source_name:
        return ""

    # 1. Kalau source_name sudah mengandung domain langsung
    domain_match = re.search(
        r"([a-zA-Z0-9\-]+\.(?:co\.id|go\.id|ac\.id|or\.id|com|id|net|org|co))",
        source_name,
    )

    if domain_match:
        return domain_match.group(1).lower()

    normalized = normalize_source_alias(source_name)

    # 2. Exact match ke database
    exact = PublisherDomainAlias.objects.filter(
        normalized_alias=normalized,
        is_active=True,
    ).first()

    if exact:
        return exact.domain.strip().lower()

    # 3. Partial match: kalau source_name mengandung alias
    aliases = PublisherDomainAlias.objects.filter(is_active=True)

    for item in aliases:
        alias_norm = item.normalized_alias or normalize_source_alias(item.alias)

        if alias_norm and alias_norm in normalized:
            return item.domain.strip().lower()

        if normalized and normalized in alias_norm:
            return item.domain.strip().lower()

    return ""


def score_search_result_link(url: str, anchor_text: str, title: str) -> float:
    if not url:
        return 0.0

    if is_blocked_domain(url):
        return 0.0

    if is_probably_non_article_url(url):
        return 0.0

    title_clean = clean_title_for_search(title).lower()
    title_slug = slugify_title(title)
    anchor_clean = clean_text(anchor_text).lower()
    url_clean = url.lower()

    if not title_clean:
        return 0.0

    score = 0.0

    # URL artikel biasanya mengandung slug judul.
    if title_slug and title_slug in url_clean:
        score += 12.0

    # Judul persis muncul di anchor.
    if title_clean[:50] and title_clean[:50] in anchor_clean:
        score += 7.0

    score += title_similarity(title_clean, anchor_clean) * 5.0

    title_words = title_words_for_match(title)

    if title_words:
        matched_words = sum(
            1 for w in title_words
            if w in anchor_clean or w in url_clean
        )
        score += (matched_words / max(len(title_words), 1)) * 6.0

    if any(hint in url_clean for hint in ARTICLE_PATH_HINTS):
        score += 2.0

    # Pola artikel RRI:
    # /sorong/regional/2361417/slug
    if re.search(r"/[a-z0-9-]+/[a-z0-9-]+/\d{5,}/", url_clean):
        score += 4.0

    return score


def extract_candidate_urls_from_raw_html(html: str, base_url: str) -> list[str]:
    candidates = []

    if not html:
        return candidates

    # URL absolut di HTML/JSON/script.
    absolute_urls = re.findall(r"https?://[^\s\"'<>\\]+", html)

    for url in absolute_urls:
        url = url.replace("\\/", "/").strip()

        if url.startswith(("http://", "https://")):
            candidates.append(url)

    # Path relatif seperti /sorong/regional/2361417/slug
    relative_paths = re.findall(
        r"(/[a-zA-Z0-9-]+/[a-zA-Z0-9-]+/\d{5,}/[a-zA-Z0-9\-]+)",
        html,
    )

    for path in relative_paths:
        candidates.append(urljoin(base_url, path))

    result = []
    seen = set()

    for url in candidates:
        if url in seen:
            continue

        seen.add(url)
        result.append(url)

    return result


def extract_best_link_from_search_page(html: str, base_url: str, title: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    candidates = []

    # 1. Ambil dari anchor HTML.
    for a in soup.find_all("a", href=True):
        href = a.get("href") or ""
        url = urljoin(base_url, href)

        if not url.startswith(("http://", "https://")):
            continue

        if is_blocked_domain(url):
            continue

        if is_probably_non_article_url(url):
            continue

        anchor_text = a.get_text(" ", strip=True)
        score = score_search_result_link(url, anchor_text, title)

        if score <= 0:
            continue

        candidates.append((score, url))

    # 2. Ambil dari raw HTML/script/JSON.
    raw_urls = extract_candidate_urls_from_raw_html(html, base_url)

    for url in raw_urls:
        if is_blocked_domain(url):
            continue

        if is_probably_non_article_url(url):
            continue

        score = score_search_result_link(url, "", title)

        if score <= 0:
            continue

        candidates.append((score, url))

    candidates.sort(key=lambda x: x[0], reverse=True)

    seen = set()

    for score, url in candidates:
        if url in seen:
            continue

        seen.add(url)

        if score >= 3.0:
            return url

    return ""


def resolve_by_publisher_search(
    title: str,
    source_name: str,
    timeout: int = 4,
    max_attempts: int = 4,
) -> dict:
    domain = source_name_to_domain(source_name, title=title)
    title_clean = clean_title_for_search(title)

    if not domain or not title_clean:
        return {
            "url": "",
            "mode": "publisher_search_failed",
            "error": "Domain publisher atau judul artikel tidak tersedia.",
        }

    query = quote_plus(title_clean)

    base_hosts = [
        f"https://{domain}",
        f"https://www.{domain}" if not domain.startswith("www.") else f"https://{domain}",
    ]

    search_paths = [
        f"/search?keyword={query}",
        f"/search?q={query}",
        f"/search?s={query}",
        f"/?s={query}",
        f"/cari?q={query}",
        f"/search/{query}",
    ]

    errors = []
    attempts = 0

    for base_host in base_hosts:
        for path in search_paths:
            attempts += 1
            if attempts > max_attempts:
                break
            search_url = f"{base_host}{path}"
            html, final_url, error = request_html(search_url, timeout=timeout)

            if error:
                errors.append(f"{search_url}: {error}")
                continue

            best_url = extract_best_link_from_search_page(
                html=html,
                base_url=final_url or search_url,
                title=title_clean,
            )

            if best_url and not is_probably_non_article_url(best_url):
                return {
                    "url": best_url,
                    "mode": "publisher_search",
                    "error": "",
                }

    return {
        "url": "",
        "mode": "publisher_search_failed",
        "error": "Publisher search tidak menemukan artikel yang cukup relevan.",
        "debug": errors[:3],
    }

def get_discovery_paths_for_domain(domain: str, query: str) -> list[str]:
    paths = DOMAIN_DISCOVERY_PATHS.get(domain, DISCOVERY_PATHS)

    result = []

    for path in paths:
        if "{query}" in path:
            result.append(path.format(query=query))
        else:
            result.append(path)

    return result


def discover_article_from_publisher(
    title: str,
    source_name: str,
    timeout: int = 4,
    max_attempts: int = 5,
) -> dict:
    """
    Fallback tambahan jika search internal publisher gagal.

    Sistem membuka beberapa halaman listing berita publisher, lalu mencari
    link artikel yang paling mirip dengan judul signal.
    """
    domain = source_name_to_domain(source_name, title=title)
    title_clean = clean_title_for_search(title)

    if not domain or not title_clean:
        return {
            "url": "",
            "mode": "publisher_discovery_failed",
            "error": "Domain publisher atau judul artikel tidak tersedia.",
        }

    query = quote_plus(title_clean)

    base_hosts = [
        f"https://{domain}",
        f"https://www.{domain}" if not domain.startswith("www.") else f"https://{domain}",
    ]

    errors = []
    attempts = 0

    for base_host in base_hosts:
        for path in get_discovery_paths_for_domain(domain, query):
            attempts += 1
            if attempts > max_attempts:
                break

            discovery_url = f"{base_host}{path}"

            html, final_url, error = request_html(discovery_url, timeout=timeout)

            if error:
                errors.append(f"{discovery_url}: {error}")
                continue

            best_url = extract_best_link_from_search_page(
                html=html,
                base_url=final_url or discovery_url,
                title=title_clean,
            )

            if best_url and not is_probably_non_article_url(best_url):
                return {
                    "url": best_url,
                    "mode": "publisher_discovery",
                    "error": "",
                }

        if attempts > max_attempts:
            break

    return {
        "url": "",
        "mode": "publisher_discovery_failed",
        "error": "Publisher discovery tidak menemukan artikel yang cukup relevan.",
        "debug": errors[:3],
    }
# =========================================================
# FETCH ARTICLE
# =========================================================

def fetch_article_text(
    url: str,
    title: str = "",
    source_name: str = "",
    timeout: int = 6,
    trusted_url: bool = False,
    skip_resolution: bool = False,
) -> dict:
    """
    Ambil teks artikel.

    Optimasi:
    - Jika skip_resolution=True, URL langsung dipakai tanpa Google resolver/search/discovery.
      Ini dipakai untuk resolved_url manual dari validator.
    - Jika URL belum resolved, baru coba Google resolver -> publisher search -> discovery.
    """

    if skip_resolution:
        resolved = {
            "url": url,
            "mode": "direct",
            "error": "",
        }
    else:
        resolved = resolve_google_news_url(
            url=url,
            title=title,
            source_name=source_name,
            timeout=timeout,
        )

    if not resolved.get("url"):
        publisher_resolved = resolve_by_publisher_search(
            title=title,
            source_name=source_name,
            timeout=4,
            max_attempts=4,
        )

        if publisher_resolved.get("url"):
            resolved = publisher_resolved
        else:
            discovery_resolved = discover_article_from_publisher(
                title=title,
                source_name=source_name,
                timeout=4,
                max_attempts=5,
            )

            if discovery_resolved.get("url"):
                resolved = discovery_resolved
            else:
                return {
                    "text": "",
                    "final_url": url or "",
                    "error": (
                        discovery_resolved.get("error")
                        or publisher_resolved.get("error")
                        or resolved.get("error")
                        or "URL artikel asli tidak ditemukan."
                    ),
                    "fetch_mode": (
                        discovery_resolved.get("mode")
                        or publisher_resolved.get("mode")
                        or resolved.get("mode")
                        or "failed"
                    ),
                    "metadata": {},
                }

    article_url = resolved["url"]

    html, final_url, error = request_html(article_url, timeout=timeout)

    if error:
        return {
            "text": "",
            "final_url": final_url or article_url,
            "error": error,
            "fetch_mode": resolved.get("mode") or "failed",
            "metadata": {},
        }

    text, metadata = extract_article_text_from_html(html)
    final_article_url = final_url or article_url

    if trusted_url:
        is_bad_article = (
            is_probably_non_article_url(final_article_url)
            or len(clean_text(text)) < 250
        )
    else:
        is_bad_article = (
            is_text_too_weak(text, title=title)
            or is_probably_non_article_url(final_article_url)
            or not is_article_relevant_to_title(
                text=text,
                title=title,
                metadata=metadata,
                url=final_article_url,
            )
        )

    if is_bad_article:
        external_links = find_external_article_links(html, final_article_url)

        for external_url in external_links[:3]:
            ext_html, ext_final_url, ext_error = request_html(
                external_url,
                timeout=4,
            )

            if ext_error:
                continue

            ext_text, ext_metadata = extract_article_text_from_html(ext_html)
            ext_final = ext_final_url or external_url

            if trusted_url:
                ext_is_valid = (
                    not is_probably_non_article_url(ext_final)
                    and len(clean_text(ext_text)) >= 250
                )
            else:
                ext_is_valid = (
                    not is_text_too_weak(ext_text, title=title)
                    and not is_probably_non_article_url(ext_final)
                    and is_article_relevant_to_title(
                        text=ext_text,
                        title=title,
                        metadata=ext_metadata,
                        url=ext_final,
                    )
                )

            if ext_is_valid:
                return {
                    "text": ext_text,
                    "final_url": ext_final,
                    "error": "",
                    "fetch_mode": f"{resolved.get('mode')}_external_link",
                    "metadata": ext_metadata,
                }

        return {
            "text": "",
            "final_url": final_article_url,
            "error": (
                "URL berhasil dibuka, tetapi halaman yang ditemukan bukan artikel yang relevan "
                "dengan judul signal atau termasuk halaman profil/redaksi/tentang-kami/search."
            ),
            "fetch_mode": resolved.get("mode") or "weak_text",
            "metadata": metadata,
        }

    return {
        "text": text,
        "final_url": final_article_url,
        "error": "",
        "fetch_mode": resolved.get("mode") or "direct",
        "metadata": metadata,
    }


# =========================================================
# 5W + 1H EXTRACTION
# =========================================================

def pick_first_matching_sentence(sentences: list[str], patterns: list[str]) -> str:
    for sentence in sentences:
        low = sentence.lower()

        for pattern in patterns:
            if re.search(pattern, low, flags=re.IGNORECASE):
                return sentence

    return ""


def extract_numbers_context(sentences: list[str]) -> list[str]:
    results = []

    for sentence in sentences:
        if re.search(r"\b\d+[\.\d]*\b", sentence):
            results.append(sentence)

        if len(results) >= 4:
            break

    return results


def build_summary(sentences: list[str], disease: str = "") -> str:
    important = []

    keywords = [
        disease.lower() if disease else "",
        "klb",
        "wabah",
        "kasus",
        "meninggal",
        "dirawat",
        "positif",
        "imunisasi",
        "vaksinasi",
        "dinkes",
        "dinas kesehatan",
        "pencegahan",
        "penanganan",
        "meningkat",
        "terinfeksi",
        "terjangkit",
        "suspek",
        "penyelidikan epidemiologi",
    ]

    keywords = [k for k in keywords if k]

    for sentence in sentences:
        low = sentence.lower()

        if any(k in low for k in keywords):
            important.append(sentence)

        if len(important) >= 4:
            break

    if not important:
        important = sentences[:3]

    return clean_text(" ".join(important))[:1400]


def build_5w1h_assessment(
    signal,
    article_text: str,
    article_url: str = "",
    fetch_mode: str = "",
    trusted_url: bool = False,
) -> dict:
    title = signal.title or ""
    source_name = signal.source.name if signal.source else ""

    if not source_name or source_name.lower() in ["google news", "google", "google rss"]:
        source_name = extract_source_from_title(title)

    disease = signal.disease_tag or "-"
    published_at = signal.published_at.strftime("%Y-%m-%d %H:%M") if signal.published_at else "-"

    location_text = signal.raw_location_text or ""

    if hasattr(signal, "primary_locations") and signal.primary_locations:
        loc = signal.primary_locations[0].location

        if loc:
            if loc.parent:
                location_text = f"{loc.display_name}, {loc.parent.display_name}"
            else:
                location_text = loc.display_name

    base_text = clean_text(article_text or signal.content or title)
    sentences = split_sentences(base_text)

    if not sentences:
        sentences = [title]

    fallback_like = is_fallback_source_text(
        base_text,
        title=title,
        trusted_url=trusted_url,
    )
    if fallback_like:
        default_unavailable = "Belum cukup data karena artikel asli belum berhasil diambil."

        if location_text and len(location_text) <= 100:
            safe_where = location_text
        else:
            safe_where = default_unavailable

        return {
            "title": title,
            "source": source_name,
            "published_at": published_at,
            "article_url": article_url,
            "fetch_mode": fetch_mode,
            "disease": disease,
            "summary": "Assessment belum memadai karena sistem belum berhasil mengambil isi artikel asli.",
            "what": default_unavailable,
            "who": default_unavailable,
            "when": published_at,
            "where": safe_where,
            "why": default_unavailable,
            "how": default_unavailable,
            "numbers_context": [],
            "assessment_quality": "low_fallback",
            "validator_note": (
                "Assessment masih rendah karena hanya memakai judul/RSS. "
                "Validator perlu membuka sumber asli atau menjalankan recrawl final_url."
            ),
        }

    summary = build_summary(sentences, disease=disease)
    numbers_context = extract_numbers_context(sentences)

    what = pick_first_matching_sentence(
        sentences,
        [
            r"\bklb\b",
            r"\bwabah\b",
            r"\bkasus\b",
            r"\bterinfeksi\b",
            r"\bterjangkit\b",
            r"\bdifteri\b",
            r"\brabies\b",
            r"\bdbd\b",
            r"\bcampak\b",
            r"\bimunisasi\b",
            r"\bvaksinasi\b",
            r"\bmeninggal\b",
            r"\bpositif\b",
            r"\bdirawat\b",
        ],
    )

    who = pick_first_matching_sentence(
        sentences,
        [
            r"\bdinkes\b",
            r"\bdinas kesehatan\b",
            r"\bkemenkes\b",
            r"\bpemerintah\b",
            r"\bwarga\b",
            r"\banak\b",
            r"\bpasien\b",
            r"\bpetugas\b",
            r"\bmasyarakat\b",
            r"\brsud\b",
            r"\brumah sakit\b",
            r"\bpuskesmas\b",
        ],
    )

    when = pick_first_matching_sentence(
        sentences,
        [
            r"\b20\d{2}\b",
            r"\bjanuari\b",
            r"\bfebruari\b",
            r"\bmaret\b",
            r"\bapril\b",
            r"\bmei\b",
            r"\bjuni\b",
            r"\bjuli\b",
            r"\bagustus\b",
            r"\bseptember\b",
            r"\boktober\b",
            r"\bnovember\b",
            r"\bdesember\b",
            r"\bhari ini\b",
            r"\bkemarin\b",
            r"\bpekan\b",
            r"\bminggu\b",
            r"\bbulan\b",
        ],
    )

    where = pick_first_matching_sentence(
        sentences,
        [
            r"\bdi\s+[A-ZÁ-ÿ]?",
            r"\bkabupaten\b",
            r"\bkota\b",
            r"\bprovinsi\b",
            r"\bkecamatan\b",
            r"\bdesa\b",
            r"\brsud\b",
            r"\bpuskesmas\b",
        ],
    )

    why = pick_first_matching_sentence(
        sentences,
        [
            r"\bkarena\b",
            r"\bakibat\b",
            r"\bdisebabkan\b",
            r"\bpemicu\b",
            r"\bfaktor\b",
            r"\bpenyebab\b",
            r"\brisiko\b",
            r"\bkontak\b",
            r"\blingkungan\b",
            r"\bimunisasi rendah\b",
        ],
    )

    how = pick_first_matching_sentence(
        sentences,
        [
            r"\bmelakukan\b",
            r"\bdilakukan\b",
            r"\bmenangani\b",
            r"\bmengimbau\b",
            r"\bupaya\b",
            r"\bpenanganan\b",
            r"\bpencegahan\b",
            r"\bvaksinasi\b",
            r"\bimunisasi\b",
            r"\bfogging\b",
            r"\bpsn\b",
            r"\bsosialisasi\b",
            r"\bkoordinasi\b",
            r"\bpenyelidikan epidemiologi\b",
        ],
    )

    default_unavailable = "Belum teridentifikasi jelas dari artikel."

    return {
        "title": title,
        "source": source_name,
        "published_at": published_at,
        "article_url": article_url,
        "fetch_mode": fetch_mode,
        "disease": disease,
        "summary": summary or title,
        "what": what or default_unavailable,
        "who": who or default_unavailable,
        "when": when or published_at,
        "where": where or location_text or default_unavailable,
        "why": why or default_unavailable,
        "how": how or default_unavailable,
        "numbers_context": numbers_context,
        "assessment_quality": "article_based_partial" if trusted_url and len(base_text) < 500 else "article_based",
        "validator_note": (
            "Assessment dibuat otomatis dari teks artikel yang berhasil diambil. "
            "Validator tetap perlu mengecek sumber apabila 5W+1H belum lengkap."
        ),
    }


def build_assessment(signal) -> dict:
    source_name = signal.source.name if signal.source else ""

    if not source_name or source_name.lower() in ["google news", "google", "google rss"]:
        source_name = extract_source_from_title(signal.title or "")

    fetch_result = {
        "text": "",
        "final_url": signal.source_url or "",
        "error": "",
        "fetch_mode": "fallback",
        "metadata": {},
    }

    manual_resolved_url = getattr(signal, "resolved_url", "") or ""
    article_url = manual_resolved_url or signal.source_url

    if article_url:
        fetch_result = fetch_article_text(
            article_url,
            title=signal.title or "",
            source_name=source_name,
            timeout=6,
            trusted_url=bool(manual_resolved_url),
            skip_resolution=bool(manual_resolved_url),
        )

    article_text = fetch_result.get("text") or ""

    if article_text and (
        not is_text_too_weak(article_text, title=signal.title or "")
        or (manual_resolved_url and len(clean_text(article_text)) >= 250)
    ):
        source_text = article_text
        status = "ok"
    else:
        source_text = signal.content or signal.title or ""
        status = "fallback"

    assessment = build_5w1h_assessment(
        signal=signal,
        article_text=source_text,
        article_url=fetch_result.get("final_url") or signal.source_url or "",
        fetch_mode=fetch_result.get("fetch_mode") or "fallback",
        trusted_url=bool(manual_resolved_url),
    )

    if fetch_result.get("error"):
        assessment["fetch_warning"] = fetch_result["error"]

    if status == "fallback":
        assessment["fallback_used"] = True
        assessment["summary"] = (
            "Assessment belum memadai karena artikel asli belum berhasil diambil. "
            "Data yang tersedia saat ini hanya berasal dari judul/RSS crawler."
        )

    return {
        "status": status if source_text else "failed",
        "assessment": assessment,
        "summary": assessment.get("summary", ""),
        "source_text": source_text[:15000],
        "error": fetch_result.get("error", ""),
    }