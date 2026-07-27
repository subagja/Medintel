import base64
import json
import re
from urllib.parse import urlparse, unquote

import requests


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    "Origin": "https://news.google.com",
    "Referer": "https://news.google.com/",
}


def is_google_news_url(url: str) -> bool:
    if not url:
        return False

    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False

    return "news.google." in host or "google." in host


def is_bad_url(url: str) -> bool:
    if not url:
        return True

    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return True

    blocked = [
        "google.",
        "news.google.",
        "gstatic.",
        "googleusercontent.",
        "accounts.google.",
        "policies.google.",
        "facebook.",
        "twitter.",
        "x.com",
        "instagram.",
        "youtube.",
        "tiktok.",
    ]

    return any(item in host for item in blocked)


def extract_google_news_id(url: str) -> str:
    """
    Extract encoded article id from:
    - https://news.google.com/rss/articles/<ID>?oc=5
    - https://news.google.com/articles/<ID>
    - https://news.google.com/read/<ID>
    """
    if not url:
        return ""

    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]

    for marker in ["articles", "read"]:
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                return unquote(parts[idx + 1].split("?")[0])

    return ""


def decode_google_news_base64(url: str) -> str:
    article_id = extract_google_news_id(url)

    if not article_id:
        return ""

    try:
        padded = article_id + ("=" * (-len(article_id) % 4))
        decoded = base64.urlsafe_b64decode(padded).decode("latin1", errors="ignore")

        urls = re.findall(r"https?://[^\s\x00-\x1f\"'<>]+", decoded)

        for candidate in urls:
            candidate = candidate.strip()

            if candidate and not is_bad_url(candidate):
                return candidate

    except Exception:
        return ""

    return ""


def decode_google_news_batchexecute(url: str, timeout: int = 10) -> str:
    """
    Resolver Google News RSS modern via batchexecute.
    Ini tidak memakai DuckDuckGo/search engine.
    """
    article_id = extract_google_news_id(url)

    if not article_id:
        return ""

    endpoint = "https://news.google.com/_/DotsSplashUi/data/batchexecute"

    payload_inner = [
        "garturlreq",
        [
            [
                "id-ID",
                "ID",
                ["FINANCE_TOP_INDICES", "WEB_TEST_1_0_0"],
                None,
                None,
                1,
                1,
                "ID:id",
                None,
                180,
                None,
                None,
                None,
                None,
                None,
                0,
                None,
                None,
                [1608992183, 723341000],
            ],
            "id-ID",
            "ID",
            1,
            [2, 3, 4, 8],
            1,
            0,
            "655000234",
            0,
            0,
            None,
            0,
        ],
        article_id,
    ]

    f_req = json.dumps([
        [
            [
                "Fbv4je",
                json.dumps(payload_inner, separators=(",", ":")),
                None,
                "generic",
            ]
        ]
    ])

    try:
        response = requests.post(
            endpoint,
            params={
                "rpcids": "Fbv4je",
                "source-path": "/rss/articles/" + article_id,
            },
            headers=HEADERS,
            data={"f.req": f_req},
            timeout=timeout,
        )

        if response.status_code >= 400:
            return ""

        text = response.text or ""

        text = (
            text.replace("\\u003d", "=")
            .replace("\\u0026", "&")
            .replace("\\/", "/")
        )

        urls = re.findall(r"https?://[^\s\"\\\]]+", text)

        for candidate in urls:
            candidate = candidate.strip()

            if candidate and not is_bad_url(candidate):
                return candidate

    except Exception:
        return ""

    return ""


def resolve_google_news_url(
    url: str,
    title: str = "",
    source_name: str = "",
    timeout: int = 10,
) -> dict:
    """
    Resolve Google News RSS URL ke URL artikel asli.

    Catatan:
    - Tidak memakai DuckDuckGo.
    - Kalau gagal, return failed agar signal_assessment.py bisa fallback ke content/title.
    """

    if not url:
        return {
            "url": "",
            "mode": "failed",
            "error": "URL kosong.",
        }

    if not is_google_news_url(url):
        return {
            "url": url,
            "mode": "direct",
            "error": "",
        }

    decoded = decode_google_news_base64(url)

    if decoded:
        return {
            "url": decoded,
            "mode": "google_news_base64",
            "error": "",
        }

    decoded = decode_google_news_batchexecute(url, timeout=timeout)

    if decoded:
        return {
            "url": decoded,
            "mode": "google_news_batchexecute",
            "error": "",
        }

    return {
        "url": "",
        "mode": "failed",
        "error": (
            "Google News RSS URL tidak berhasil di-resolve menjadi URL artikel asli. "
            "Resolver tidak memakai search engine karena koneksi lokal menolak akses search eksternal."
        ),
    }