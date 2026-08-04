from __future__ import annotations

import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
import truststore
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from apps.sources.models import Source


DEFAULT_USER_AGENT = (
    "MedIntelBot/1.0 "
    "(Medical Intelligence OSINT Research Crawler)"
)

DEFAULT_ACCEPT = (
    "text/html,"
    "application/xhtml+xml,"
    "application/xml;q=0.9,"
    "*/*;q=0.8"
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
        source: Source,
    ) -> None:
        self.source = source

        self.user_agent = (
            source.user_agent.strip()
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
                allow_redirects=True,
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
