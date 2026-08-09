from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

GOOGLE_NEWS_RPC_ID = "Fbv4je"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,4096}$")
_SIGNATURE_RE = re.compile(r"^[A-Za-z0-9_-]{4,512}$")


@dataclass(frozen=True)
class GoogleNewsDecodingParams:
    article_token: str
    signature: str
    timestamp: int


class _GoogleNewsParamsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.signature = ""
        self.timestamp = ""

    def handle_starttag(self, _tag: str, attrs) -> None:
        if self.signature and self.timestamp:
            return
        values = dict(attrs)
        signature = str(values.get("data-n-a-sg") or "").strip()
        timestamp = str(values.get("data-n-a-ts") or "").strip()
        if signature and timestamp:
            self.signature = signature
            self.timestamp = timestamp


def extract_google_news_article_token(url: str) -> str:
    """Ambil token opaque hanya dari bentuk URL artikel Google News."""
    parsed = urlsplit(url)
    if (parsed.hostname or "").lower().rstrip(".") != "news.google.com":
        return ""

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[-2] not in {"articles", "read"}:
        return ""

    token = path_parts[-1].strip()
    return token if _TOKEN_RE.fullmatch(token) else ""


def decode_legacy_google_news_url(url: str) -> str:
    """Dekode token lama yang masih menyimpan URL penerbit secara lokal.

    Token generasi baru berisi id opaque, bukan URL. Untuk bentuk tersebut
    fungsi ini sengaja mengembalikan string kosong agar pemanggil memakai
    resolver RPC resmi dari halaman Google News.
    """
    token = extract_google_news_article_token(url)
    if not token:
        return ""

    padded = token + ("=" * (-len(token) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded)
    except (ValueError, TypeError):
        return ""

    prefix = b'\x08\x13"'
    if decoded.startswith(prefix):
        decoded = decoded[len(prefix) :]

    # Panjang string protobuf memakai varint. Batasi pembacaan agar token
    # rusak tidak membuat indeks bergerak tanpa kendali.
    length = 0
    shift = 0
    cursor = 0
    for byte in decoded[:5]:
        cursor += 1
        length |= (byte & 0x7F) << shift
        if not byte & 0x80:
            break
        shift += 7
    else:
        return ""

    if length < 8 or cursor + length > len(decoded):
        return ""

    try:
        candidate = decoded[cursor : cursor + length].decode("utf-8")
    except UnicodeDecodeError:
        return ""

    return candidate if candidate.startswith(("http://", "https://")) else ""


def extract_google_news_decoding_params(
    *,
    article_url: str,
    html: str,
) -> GoogleNewsDecodingParams | None:
    """Baca parameter decoder yang Google sematkan pada halaman artikel."""
    token = extract_google_news_article_token(article_url)
    if not token or not html:
        return None

    parser = _GoogleNewsParamsParser()
    parser.feed(html)
    signature = parser.signature
    timestamp_text = parser.timestamp
    if not _SIGNATURE_RE.fullmatch(signature) or not timestamp_text.isdigit():
        return None

    timestamp = int(timestamp_text)
    if timestamp < 1:
        return None

    return GoogleNewsDecodingParams(
        article_token=token,
        signature=signature,
        timestamp=timestamp,
    )


def build_google_news_rpc_form(
    params: GoogleNewsDecodingParams,
) -> dict[str, str]:
    """Bangun form RPC satu artikel tanpa menyisipkan string mentah."""
    request_context = [
        [
            "id-ID",
            "ID",
            ["id-ID", "ID"],
            None,
            None,
            1,
            1,
            "ID:id",
            None,
            1,
            None,
            None,
            None,
            None,
            None,
            0,
            1,
        ],
        "id-ID",
        "ID",
        1,
        [1, 1, 1],
        1,
        1,
        None,
        0,
        0,
        None,
        0,
    ]
    inner_request = [
        "garturlreq",
        request_context,
        params.article_token,
        params.timestamp,
        params.signature,
    ]
    rpc_call = [
        GOOGLE_NEWS_RPC_ID,
        json.dumps(inner_request, separators=(",", ":")),
        None,
        "generic",
    ]
    return {
        "f.req": json.dumps([[rpc_call]], separators=(",", ":")),
    }


def _find_garturl_response(value) -> str:
    if isinstance(value, list):
        if (
            len(value) >= 2
            and value[0] == "garturlres"
            and isinstance(value[1], str)
            and value[1].startswith(("http://", "https://"))
        ):
            return value[1]
        for item in value:
            result = _find_garturl_response(item)
            if result:
                return result
        return ""

    if isinstance(value, dict):
        for item in value.values():
            result = _find_garturl_response(item)
            if result:
                return result
        return ""

    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("[", "{")):
            try:
                nested = json.loads(stripped)
            except json.JSONDecodeError:
                return ""
            return _find_garturl_response(nested)
    return ""


def extract_publisher_url_from_rpc_response(text: str) -> str:
    """Ambil URL hanya dari record ``garturlres`` pada respons batchexecute."""
    if not text:
        return ""

    for line in text.splitlines():
        candidate = line.strip()
        if not candidate.startswith(("[", "{")):
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        result = _find_garturl_response(parsed)
        if result:
            return result

    # Beberapa respons membungkus JSON batin sebagai string escaped pada
    # sebuah frame yang tidak nyaman diparsing per baris.
    match = re.search(
        r'\[\\"garturlres\\",\\"((?:\\\\.|[^"\\])*)\\"',
        text,
    )
    if match:
        try:
            result = json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError:
            return ""
        if result.startswith(("http://", "https://")):
            return result
    return ""
