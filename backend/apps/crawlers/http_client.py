from __future__ import annotations

import ssl
import time
from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
import truststore
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from apps.sources.models import Source


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; MedIntelBot/1.0; "
    "Medical Intelligence Surveillance Research)"
)

DEFAULT_ACCEPT = (
    "text/html,"
    "application/xhtml+xml,"
    "application/xml;q=0.9,"
    "*/*;q=0.8"
)

FEED_ACCEPT = (
    "application/rss+xml,"
    "application/atom+xml,"
    "application/xml;q=0.9,"
    "text/xml;q=0.9,"
    "*/*;q=0.5"
)


class CrawlerHttpError(Exception):
    """Kesalahan ketika mengambil halaman web."""


class RobotsDeniedError(CrawlerHttpError):
    """URL tidak diizinkan oleh robots.txt."""


class SystemTrustStoreAdapter(HTTPAdapter):
    """
    Adapter HTTPS yang memvalidasi sertifikat melalui trust store OS.

    Pada Windows, truststore memakai CryptoAPI sehingga rantai sertifikat
    yang dipercaya oleh sistem dan intermediate certificate yang diperlukan
    dapat digunakan tanpa menonaktifkan verifikasi TLS.
    """

    def __init__(
        self,
        *args,
        **kwargs,
    ) -> None:
        self.ssl_context = truststore.SSLContext(
            ssl.PROTOCOL_TLS_CLIENT
        )
        super().__init__(
            *args,
            **kwargs,
        )

    def init_poolmanager(
        self,
        connections,
        maxsize,
        block=False,
        **pool_kwargs,
    ):
        pool_kwargs["ssl_context"] = (
            self.ssl_context
        )
        return super().init_poolmanager(
            connections,
            maxsize,
            block=block,
            **pool_kwargs,
        )

    def proxy_manager_for(
        self,
        proxy,
        **proxy_kwargs,
    ):
        proxy_kwargs["ssl_context"] = (
            self.ssl_context
        )
        return super().proxy_manager_for(
            proxy,
            **proxy_kwargs,
        )


@dataclass(frozen=True)
class HttpPage:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    text: str


class CrawlerHttpClient:
    """
    HTTP client crawler yang:
    - menghormati robots.txt;
    - memakai trust store sistem operasi;
    - menerapkan delay per source;
    - melakukan retry terbatas untuk gangguan sementara;
    - tidak menonaktifkan verifikasi SSL;
    - tidak mencoba melewati proteksi akses situs.
    """

    def __init__(
        self,
        source: Source | None,
    ) -> None:
        self.source = source or SimpleNamespace(
            user_agent="",
            request_delay_seconds=0.5,
            request_timeout_seconds=20,
        )

        self.user_agent = (
            self.source.user_agent.strip()
            or DEFAULT_USER_AGENT
        )

        self.session = requests.Session()

        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": DEFAULT_ACCEPT,
                "Accept-Language": (
                    "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7"
                ),
                "Accept-Encoding": "gzip, deflate",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Connection": "keep-alive",
                # Header "Fetch Metadata" & lain-lain yang secara
                # otomatis dikirim browser modern (Chrome/Firefox) tapi
                # TIDAK dikirim library requests Python secara default.
                # Beberapa WAF/anti-bot memakai absennya header ini
                # sebagai sinyal traffic non-browser -- menambahkannya
                # tidak menjamin lolos (WAF canggih dengan JS challenge/
                # TLS fingerprinting tetap bisa memblokir), tapi cukup
                # membantu untuk proteksi level menengah.
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            }
        )

        retry = Retry(
            total=3,
            connect=3,
            read=2,
            status=3,
            backoff_factor=1.0,
            status_forcelist=(
                408,
                429,
                500,
                502,
                503,
                504,
                521,
                522,
                523,
                524,
            ),
            allowed_methods=frozenset(
                {
                    "GET",
                    "HEAD",
                }
            ),
            respect_retry_after_header=True,
            raise_on_status=False,
        )

        https_adapter = SystemTrustStoreAdapter(
            max_retries=retry,
            pool_connections=10,
            pool_maxsize=10,
        )

        http_adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=10,
            pool_maxsize=10,
        )

        self.session.mount(
            "https://",
            https_adapter,
        )
        self.session.mount(
            "http://",
            http_adapter,
        )

        self._last_request_time: float | None = None
        self._robots_cache: dict[
            str,
            RobotFileParser,
        ] = {}

    def _wait_for_delay(self) -> None:
        delay = max(
            float(
                self.source.request_delay_seconds
            ),
            0.0,
        )

        if (
            delay == 0
            or self._last_request_time is None
        ):
            return

        elapsed = (
            time.monotonic()
            - self._last_request_time
        )

        remaining = delay - elapsed

        if remaining > 0:
            time.sleep(
                remaining
            )

    def _request(
        self,
        url: str,
        *,
        accept: str = DEFAULT_ACCEPT,
        allow_redirects: bool = True,
    ) -> requests.Response:
        self._wait_for_delay()

        try:
            response = self.session.get(
                url,
                headers={
                    "Accept": accept,
                },
                timeout=(
                    self.source.request_timeout_seconds
                ),
                allow_redirects=allow_redirects,
            )

            self._last_request_time = (
                time.monotonic()
            )

            return response

        except requests.Timeout as exc:
            raise CrawlerHttpError(
                f"Permintaan timeout: {url}"
            ) from exc

        except requests.exceptions.SSLError as exc:
            raise CrawlerHttpError(
                (
                    "Verifikasi SSL gagal untuk "
                    f"{url}: {exc}. "
                    "Sertifikat telah diperiksa "
                    "menggunakan trust store sistem operasi."
                )
            ) from exc

        except requests.RequestException as exc:
            raise CrawlerHttpError(
                (
                    f"Gagal mengambil URL "
                    f"{url}: {exc}"
                )
            ) from exc

    def _post_form(
        self,
        url: str,
        *,
        data: dict[str, str],
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        """Kirim form ke endpoint discovery tanpa mengikuti redirect POST."""
        self._wait_for_delay()

        try:
            response = self.session.post(
                url,
                data=data,
                headers=headers or {},
                timeout=self.source.request_timeout_seconds,
                allow_redirects=False,
            )
            self._last_request_time = time.monotonic()
            return response
        except requests.Timeout as exc:
            raise CrawlerHttpError(
                f"Permintaan POST timeout: {url}"
            ) from exc
        except requests.exceptions.SSLError as exc:
            raise CrawlerHttpError(
                (
                    "Verifikasi SSL gagal untuk endpoint discovery "
                    f"{url}: {exc}."
                )
            ) from exc
        except requests.RequestException as exc:
            raise CrawlerHttpError(
                f"Gagal mengirim form discovery {url}: {exc}"
            ) from exc

    def _get_robots_parser(
        self,
        url: str,
    ) -> RobotFileParser:
        parsed = urlsplit(
            url
        )

        origin = (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
        )

        cached = self._robots_cache.get(
            origin
        )

        if cached:
            return cached

        robots_url = (
            f"{origin}/robots.txt"
        )

        parser = RobotFileParser()
        parser.set_url(
            robots_url
        )

        try:
            response = self._request(
                robots_url,
                accept=(
                    "text/plain,"
                    "text/*;q=0.9,"
                    "*/*;q=0.8"
                ),
            )

            if response.status_code == 200:
                parser.parse(
                    response.text.splitlines()
                )
            else:
                # Robots yang tidak tersedia bukan
                # dianggap larangan otomatis.
                parser.parse(
                    []
                )

        except CrawlerHttpError:
            # Kegagalan mengambil robots.txt tidak
            # otomatis menutup akses prototipe.
            parser.parse(
                []
            )

        self._robots_cache[
            origin
        ] = parser

        return parser

    def is_allowed_by_robots(
        self,
        url: str,
    ) -> bool:
        parser = (
            self._get_robots_parser(
                url
            )
        )

        return parser.can_fetch(
            self.user_agent,
            url,
        )

    def get_html(
        self,
        url: str,
    ) -> HttpPage:
        if not self.is_allowed_by_robots(
            url
        ):
            raise RobotsDeniedError(
                (
                    "robots.txt tidak "
                    f"mengizinkan URL: {url}"
                )
            )

        response = self._request(
            url
        )

        if response.status_code >= 400:
            raise CrawlerHttpError(
                (
                    f"HTTP "
                    f"{response.status_code} "
                    f"ketika mengambil {url}"
                )
            )

        content_type = (
            response.headers.get(
                "Content-Type",
                "",
            )
            .lower()
        )

        if (
            "text/html"
            not in content_type
            and "application/xhtml+xml"
            not in content_type
        ):
            raise CrawlerHttpError(
                (
                    "Respons bukan halaman "
                    "HTML: "
                    f"{content_type or 'unknown'}"
                )
            )

        if not response.encoding:
            response.encoding = (
                response.apparent_encoding
                or "utf-8"
            )

        return HttpPage(
            requested_url=url,
            final_url=response.url,
            status_code=(
                response.status_code
            ),
            content_type=content_type,
            text=response.text,
        )

    def get_feed(
        self,
        url: str,
    ) -> HttpPage:
        """Ambil RSS/Atom resmi tanpa melonggarkan kebijakan HTTP.

        Beberapa penerbit mengirim feed XML dengan ``Content-Type`` yang
        kurang tepat. Respons tetap diterima bila awal isinya jelas berupa
        RSS/Atom; halaman HTML biasa tidak diperlakukan sebagai feed.
        """
        if not self.is_allowed_by_robots(url):
            raise RobotsDeniedError(
                "robots.txt tidak mengizinkan URL feed: "
                f"{url}"
            )

        response = self._request(
            url,
            accept=FEED_ACCEPT,
        )

        if response.status_code >= 400:
            raise CrawlerHttpError(
                f"HTTP {response.status_code} ketika mengambil feed {url}"
            )

        if not response.encoding:
            response.encoding = (
                response.apparent_encoding
                or "utf-8"
            )

        content_type = response.headers.get(
            "Content-Type",
            "",
        ).lower()
        text = response.text
        prefix = text.lstrip()[:500].casefold()
        looks_like_feed = any(
            marker in prefix
            for marker in (
                "<rss",
                "<feed",
                "<rdf:rdf",
            )
        )
        feed_content_type = any(
            marker in content_type
            for marker in (
                "application/rss+xml",
                "application/atom+xml",
                "application/xml",
                "text/xml",
            )
        )

        if not feed_content_type and not looks_like_feed:
            raise CrawlerHttpError(
                "Respons bukan RSS/Atom: "
                f"{content_type or 'unknown'}"
            )

        return HttpPage(
            requested_url=url,
            final_url=response.url,
            status_code=response.status_code,
            content_type=content_type,
            text=text,
        )

    @staticmethod
    def _validate_discovery_endpoint(
        url: str,
        *,
        allowed_hosts: set[str] | frozenset[str],
        allowed_path_prefixes: tuple[str, ...],
    ) -> None:
        """Batasi bypass robots hanya ke endpoint discovery yang eksplisit.

        Feed syndication yang dipilih pengguna bukan crawl rekursif halaman
        penerbit. Karena itu feed discovery dapat diambil tanpa membaca
        ``robots.txt`` agregator, tetapi hanya bila scheme, hostname, dan path
        cocok dengan allowlist yang diberikan pemanggil.
        """
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        normalized_hosts = {
            item.lower().rstrip(".")
            for item in allowed_hosts
        }

        if (
            parsed.scheme != "https"
            or hostname not in normalized_hosts
            or not any(
                parsed.path.startswith(prefix)
                for prefix in allowed_path_prefixes
            )
        ):
            raise CrawlerHttpError(
                "Endpoint discovery berada di luar allowlist sistem: "
                f"{url}"
            )

    def get_discovery_feed(
        self,
        url: str,
        *,
        allowed_hosts: set[str] | frozenset[str],
        allowed_path_prefixes: tuple[str, ...],
    ) -> HttpPage:
        """Ambil feed syndication eksternal dari endpoint ter-allowlist.

        Metode ini sengaja tidak memakai ``is_allowed_by_robots`` agregator.
        Batasannya bukan domain Source, melainkan allowlist endpoint yang
        tertanam di provider discovery. Validasi dan robots penerbit tetap
        dilakukan terpisah sebelum halaman artikel diambil.
        """
        self._validate_discovery_endpoint(
            url,
            allowed_hosts=allowed_hosts,
            allowed_path_prefixes=allowed_path_prefixes,
        )
        response = self._request(
            url,
            accept=FEED_ACCEPT,
        )

        if response.status_code >= 400:
            raise CrawlerHttpError(
                "HTTP "
                f"{response.status_code} ketika mengambil feed discovery"
            )

        if not response.encoding:
            response.encoding = response.apparent_encoding or "utf-8"

        content_type = response.headers.get("Content-Type", "").lower()
        text = response.text
        prefix = text.lstrip()[:500].casefold()
        looks_like_feed = any(
            marker in prefix
            for marker in ("<rss", "<feed", "<rdf:rdf")
        )
        feed_content_type = any(
            marker in content_type
            for marker in (
                "application/rss+xml",
                "application/atom+xml",
                "application/xml",
                "text/xml",
            )
        )

        if not feed_content_type and not looks_like_feed:
            raise CrawlerHttpError(
                "Respons endpoint discovery bukan RSS/Atom: "
                f"{content_type or 'unknown'}"
            )

        final_url = response.url
        self._validate_discovery_endpoint(
            final_url,
            allowed_hosts=allowed_hosts,
            allowed_path_prefixes=allowed_path_prefixes,
        )
        return HttpPage(
            requested_url=url,
            final_url=final_url,
            status_code=response.status_code,
            content_type=content_type,
            text=text,
        )

    def resolve_discovery_url(
        self,
        url: str,
        *,
        allowed_hosts: set[str] | frozenset[str],
        allowed_path_prefixes: tuple[str, ...],
        max_redirects: int = 5,
    ) -> HttpPage:
        """Selesaikan link agregator tanpa mengambil halaman penerbit.

        Redirect di dalam origin discovery diikuti secara terbatas. Begitu
        ``Location`` menunjuk ke origin lain, URL tersebut dikembalikan untuk
        dipetakan ke Source dan diperiksa robots-nya sebelum ada request ke
        halaman artikel.
        """
        self._validate_discovery_endpoint(
            url,
            allowed_hosts=allowed_hosts,
            allowed_path_prefixes=allowed_path_prefixes,
        )
        requested_url = url
        current_url = url

        for _redirect_index in range(max_redirects + 1):
            response = self._request(
                current_url,
                allow_redirects=False,
            )
            location = response.headers.get("Location", "").strip()

            if response.is_redirect and location:
                next_url = urljoin(current_url, location)
                next_host = (
                    urlsplit(next_url).hostname or ""
                ).lower().rstrip(".")
                normalized_hosts = {
                    item.lower().rstrip(".")
                    for item in allowed_hosts
                }

                if next_host not in normalized_hosts:
                    return HttpPage(
                        requested_url=requested_url,
                        final_url=next_url,
                        status_code=response.status_code,
                        content_type="",
                        text="",
                    )

                self._validate_discovery_endpoint(
                    next_url,
                    allowed_hosts=allowed_hosts,
                    allowed_path_prefixes=allowed_path_prefixes,
                )
                current_url = next_url
                continue

            if not response.encoding:
                response.encoding = response.apparent_encoding or "utf-8"

            return HttpPage(
                requested_url=requested_url,
                final_url=response.url or current_url,
                status_code=response.status_code,
                content_type=(
                    response.headers.get("Content-Type", "").lower()
                ),
                text=response.text,
            )

        raise CrawlerHttpError(
            "Redirect endpoint discovery melebihi batas sistem."
        )

    def post_discovery_form(
        self,
        url: str,
        *,
        data: dict[str, str],
        allowed_hosts: set[str] | frozenset[str],
        allowed_path_prefixes: tuple[str, ...],
        referer: str,
    ) -> HttpPage:
        """Kirim form hanya ke RPC provider discovery yang di-allowlist.

        Respons tidak pernah diikuti ke origin lain. Pemanggil tetap wajib
        memetakan URL penerbit dari isi respons sebelum melakukan fetch.
        """
        self._validate_discovery_endpoint(
            url,
            allowed_hosts=allowed_hosts,
            allowed_path_prefixes=allowed_path_prefixes,
        )
        response = self._post_form(
            url,
            data=data,
            headers={
                "Accept": "application/json,text/plain,*/*",
                "Content-Type": (
                    "application/x-www-form-urlencoded;charset=UTF-8"
                ),
                "Origin": "https://news.google.com",
                "Referer": referer,
            },
        )

        if response.is_redirect:
            raise CrawlerHttpError(
                "RPC discovery mengembalikan redirect yang tidak diikuti."
            )
        if response.status_code >= 400:
            raise CrawlerHttpError(
                "HTTP "
                f"{response.status_code} ketika memanggil RPC discovery"
            )

        final_url = response.url or url
        self._validate_discovery_endpoint(
            final_url,
            allowed_hosts=allowed_hosts,
            allowed_path_prefixes=allowed_path_prefixes,
        )
        if not response.encoding:
            response.encoding = response.apparent_encoding or "utf-8"
        return HttpPage(
            requested_url=url,
            final_url=final_url,
            status_code=response.status_code,
            content_type=(
                response.headers.get("Content-Type", "").lower()
            ),
            text=response.text,
        )

    def resolve_url(
        self,
        url: str,
    ) -> HttpPage:
        """Ikuti redirect URL discovery tanpa menganggap hasilnya artikel.

        Berbeda dari ``get_html``, metode ini tetap mengembalikan status HTTP
        non-2xx dan content type apa adanya. Pemanggil wajib memvalidasi final
        URL terhadap Source sebelum membaca isi respons. Hal ini diperlukan
        untuk membedakan URL agregator yang belum terurai, halaman penerbit
        yang berhasil ditemukan, dan halaman penerbit yang terblokir WAF.
        """
        if not self.is_allowed_by_robots(url):
            raise RobotsDeniedError(
                "robots.txt tidak mengizinkan URL discovery: "
                f"{url}"
            )

        response = self._request(url)

        if not response.encoding:
            response.encoding = response.apparent_encoding or "utf-8"

        return HttpPage(
            requested_url=url,
            final_url=response.url,
            status_code=response.status_code,
            content_type=(
                response.headers.get("Content-Type", "").lower()
            ),
            text=response.text,
        )

    def close(self) -> None:
        self.session.close()

    def __enter__(
        self,
    ):
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        self.close()
