from collections.abc import Iterable
from datetime import datetime

from apps.ingestion.dto import ArticlePayload

from ..base import BaseCrawler


class StaticTestCrawler(BaseCrawler):
    source_code = "media-uji"

    def crawl(self) -> Iterable[ArticlePayload]:
        yield ArticlePayload(
            source_code=self.source_code,
            url="https://example.com/read/uji-dbd-001",
            title="Peningkatan kasus demam berdarah di wilayah uji",
            content=(
                "Dinas kesehatan melaporkan adanya peningkatan kasus "
                "demam berdarah di wilayah uji. Petugas melakukan "
                "penyelidikan epidemiologi, pengendalian vektor, dan "
                "edukasi masyarakat untuk mencegah penyebaran lebih lanjut."
            ),
            published_at=datetime.fromisoformat(
                "2026-07-28T08:00:00+07:00"
            ),
            author="Redaksi Media Uji",
            excerpt=(
                "Kasus demam berdarah meningkat dan sedang ditangani "
                "oleh dinas kesehatan."
            ),
            metadata={
                "crawler": "StaticTestCrawler",
                "category": "kesehatan",
            },
        )

        yield ArticlePayload(
            source_code=self.source_code,
            url="https://example.com/read/uji-polio-001",
            title="Petugas menindaklanjuti temuan kasus polio",
            content=(
                "Petugas kesehatan menindaklanjuti temuan kasus polio "
                "dengan melakukan pelacakan kontak, pemeriksaan lingkungan, "
                "dan penguatan imunisasi anak pada wilayah terdampak."
            ),
            published_at=datetime.fromisoformat(
                "2026-07-28T09:00:00+07:00"
            ),
            author="Redaksi Media Uji",
            metadata={
                "crawler": "StaticTestCrawler",
                "category": "kesehatan",
            },
        )