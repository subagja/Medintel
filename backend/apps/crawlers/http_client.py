import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

from apps.sources.models import Source


DEFAULT_USER_AGENT = (
    "MedIntelBot/1.0 "
    "(Medical Intelligence OSINT Research Crawler)"
)


class CrawlerHttpError(Exception):
    """Kesalahan ketika mengambil halaman web."""


class RobotsDeniedError(CrawlerHttpError):
    """URL tidak diizinkan oleh robots.txt."""


@dataclass(frozen=True)
class HttpPage:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    text: str


class CrawlerHttpClient:
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
                "Accept": (
                    "text/html,"
                    "application/xhtml+xml,"
                    "application/xml;q=0.9,"
                    "*/*;q=0.8"
                ),
                "Accept-Language": (
                    "id-ID,id;q=0.9,en;q=0.7"
                ),
                "Connection": "keep-alive",
            }
        )

        self._last_request_time: float | None = None
        self._robots_cache: dict[str, RobotFileParser] = {}

    def _wait_for_delay(self) -> None:
        delay = max(
            float(self.source.request_delay_seconds),
            0.0,
        )

        if delay == 0 or self._last_request_time is None:
            return

        elapsed = (
            time.monotonic()
            - self._last_request_time
        )

        remaining = delay - elapsed

        if remaining > 0:
            time.sleep(remaining)

    def _get_robots_parser(
        self,
        url: str,
    ) -> RobotFileParser:
        parsed = urlsplit(url)

        origin = (
            f"{parsed.scheme}://{parsed.netloc}"
        )

        cached = self._robots_cache.get(origin)

        if cached:
            return cached

        robots_url = f"{origin}/robots.txt"

        parser = RobotFileParser()
        parser.set_url(robots_url)

        try:
            self._wait_for_delay()

            response = self.session.get(
                robots_url,
                timeout=self.source.request_timeout_seconds,
                allow_redirects=True,
            )

            self._last_request_time = time.monotonic()

            if response.status_code == 200:
                parser.parse(
                    response.text.splitlines()
                )
            else:
                # Jika robots.txt tidak tersedia, tidak dianggap
                # sebagai larangan otomatis.
                parser.parse([])
        except requests.RequestException:
            # Kegagalan mengambil robots.txt tidak langsung
            # menghentikan prototipe crawler.
            parser.parse([])

        self._robots_cache[origin] = parser

        return parser

    def is_allowed_by_robots(
        self,
        url: str,
    ) -> bool:
        parser = self._get_robots_parser(
            url
        )

        return parser.can_fetch(
            self.user_agent,
            url,
        )

    def get_html(
        self,
        url: str,
    ) -> HttpPage:
        if not self.is_allowed_by_robots(url):
            raise RobotsDeniedError(
                f"robots.txt tidak mengizinkan URL: {url}"
            )

        self._wait_for_delay()

        try:
            response = self.session.get(
                url,
                timeout=self.source.request_timeout_seconds,
                allow_redirects=True,
            )

            self._last_request_time = time.monotonic()

        except requests.Timeout as exc:
            raise CrawlerHttpError(
                f"Permintaan timeout: {url}"
            ) from exc

        except requests.RequestException as exc:
            raise CrawlerHttpError(
                f"Gagal mengambil URL {url}: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise CrawlerHttpError(
                (
                    f"HTTP {response.status_code} "
                    f"ketika mengambil {url}"
                )
            )

        content_type = response.headers.get(
            "Content-Type",
            "",
        ).lower()

        if (
            "text/html" not in content_type
            and "application/xhtml+xml"
            not in content_type
        ):
            raise CrawlerHttpError(
                (
                    "Respons bukan halaman HTML: "
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
            status_code=response.status_code,
            content_type=content_type,
            text=response.text,
        )

    def close(self) -> None:
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        self.close()