from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from apps.articles.models import Article
from apps.collection.models import (
    CollectionJob,
    CollectionJobItem,
)
from apps.entities.models import (
    Disease,
    DiseaseAlias,
    SurveillanceDisease,
    SurveillanceProgram,
)
from apps.sources.models import (
    Source,
    SourceUrlPattern,
)

from .implementations.static import (
    StaticTestCrawler,
)
from .real_crawler import (
    GenericHtmlCrawler,
)
from .services import run_crawler


class StaticCrawlerTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media Uji",
            code="media-uji",
            domain="example.com",
            base_url="https://example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

        SourceUrlPattern.objects.create(
            source=self.source,
            pattern="/read/",
            pattern_type=SourceUrlPattern.PatternType.ALLOW,
        )

    def test_static_crawler_creates_articles(self):
        result = run_crawler(
            StaticTestCrawler()
        )

        self.assertEqual(result.total_found, 2)
        self.assertEqual(result.total_created, 2)
        self.assertEqual(result.total_duplicate, 0)
        self.assertEqual(result.total_rejected, 0)
        self.assertEqual(result.total_failed, 0)

        self.assertEqual(
            Article.objects.count(),
            2,
        )

    def test_static_crawler_detects_duplicates(self):
        first_result = run_crawler(
            StaticTestCrawler()
        )

        second_result = run_crawler(
            StaticTestCrawler()
        )

        self.assertEqual(first_result.total_created, 2)
        self.assertEqual(second_result.total_created, 0)
        self.assertEqual(second_result.total_duplicate, 2)

        self.assertEqual(
            Article.objects.count(),
            2,
        )

    def test_static_crawler_creates_collection_history(self):
        result = run_crawler(
            StaticTestCrawler(),
            trigger_type="test",
        )

        self.assertEqual(result.total_created, 2)

        self.assertEqual(
            CollectionJob.objects.count(),
            1,
        )

        job = CollectionJob.objects.get()

        self.assertEqual(
            job.status,
            CollectionJob.Status.COMPLETED,
        )
        self.assertEqual(job.total_found, 2)
        self.assertEqual(job.total_created, 2)
        self.assertEqual(job.total_duplicate, 0)
        self.assertEqual(job.total_rejected, 0)
        self.assertEqual(job.total_failed, 0)
        self.assertEqual(job.trigger_type, "test")
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)

        self.assertEqual(
            CollectionJobItem.objects.count(),
            2,
        )

        self.assertEqual(
            CollectionJobItem.objects.filter(
                status=CollectionJobItem.Status.CREATED,
            ).count(),
            2,
        )

    def test_second_run_records_duplicate_items(self):
        run_crawler(
            StaticTestCrawler(),
            trigger_type="test",
        )

        second_result = run_crawler(
            StaticTestCrawler(),
            trigger_type="test",
        )

        self.assertEqual(
            second_result.total_duplicate,
            2,
        )

        second_job = CollectionJob.objects.order_by(
            "-created_at"
        ).first()

        self.assertIsNotNone(second_job)
        self.assertEqual(second_job.total_found, 2)
        self.assertEqual(second_job.total_created, 0)
        self.assertEqual(second_job.total_duplicate, 2)

        self.assertEqual(
            second_job.items.filter(
                status=CollectionJobItem.Status.DUPLICATE,
            ).count(),
            2,
        )


class GenericHtmlCrawlerSurveillanceTests(
    TestCase
):
    def setUp(self):
        self.program = (
            SurveillanceProgram.objects.create(
                name=(
                    "Program Surveilans "
                    "Crawler Uji"
                ),
                code=(
                    "program-surveilans-"
                    "crawler-uji"
                ),
                status=(
                    SurveillanceProgram.Status.ACTIVE
                ),
            )
        )

        self.disease = Disease.objects.create(
            name="Demam Berdarah Dengue",
            canonical_name="Dengue",
            code="dbd-crawler-test",
            category="Penyakit Menular",
            is_priority=True,
            is_active=True,
        )

        DiseaseAlias.objects.create(
            disease=self.disease,
            alias="DBD",
            language="id",
            is_active=True,
        )

        SurveillanceDisease.objects.create(
            program=self.program,
            disease=self.disease,
            official_name=(
                "Demam Berdarah Dengue"
            ),
            category="Penyakit Menular",
            is_active=True,
        )

        self.source = Source.objects.create(
            name="Media HTML Crawler Uji",
            code="media-html-crawler-uji",
            domain="example.com",
            base_url="https://example.com",
            source_type=(
                Source.SourceType.NATIONAL_MEDIA
            ),
            is_verified=True,
            is_active=True,
            crawl_enabled=True,
            allow_subdomains=False,
        )

        SourceUrlPattern.objects.create(
            source=self.source,
            pattern="/read/",
            pattern_type=(
                SourceUrlPattern.PatternType.ALLOW
            ),
            match_type=(
                SourceUrlPattern.MatchType.PREFIX
            ),
            priority=100,
            is_active=True,
        )

        self.crawler = GenericHtmlCrawler(
            source_code=self.source.code,
            limit=1,
        )

    def build_mock_client(
        self,
        *,
        final_url: str,
    ):
        return SimpleNamespace(
            get_html=lambda url: (
                SimpleNamespace(
                    text="<html></html>",
                    final_url=final_url,
                )
            ),
        )

    @patch(
        "apps.crawlers.real_crawler."
        "parse_article_html"
    )
    def test_accepts_article_with_disease_alias(
        self,
        mock_parse_article_html,
    ):
        mock_parse_article_html.return_value = (
            SimpleNamespace(
                title="Kasus DBD meningkat",
                content=(
                    "Dinas Kesehatan melaporkan "
                    "peningkatan kasus DBD di wilayah "
                    "tersebut. Pemantauan dan tindakan "
                    "pengendalian masih dilakukan oleh "
                    "petugas kesehatan setempat. "
                    "Masyarakat diminta menjaga "
                    "kebersihan lingkungan."
                ),
                published_at=datetime(
                    2026,
                    7,
                    29,
                    8,
                    0,
                ),
                author="Redaksi Kesehatan",
                canonical_url=(
                    "https://example.com/"
                    "read/kasus-dbd"
                ),
                metadata={
                    "parser": "test",
                },
            )
        )

        client = self.build_mock_client(
            final_url=(
                "https://example.com/"
                "read/kasus-dbd"
            ),
        )

        payload = self.crawler._parse_article(
            source=self.source,
            client=client,
            article_url=(
                "https://example.com/"
                "read/kasus-dbd"
            ),
        )

        self.assertIsNotNone(
            payload
        )

        self.assertEqual(
            payload.source_code,
            self.source.code,
        )

        self.assertEqual(
            payload.title,
            "Kasus DBD meningkat",
        )

        surveillance = (
            payload.metadata[
                "surveillance"
            ]
        )

        self.assertTrue(
            surveillance["is_relevant"]
        )

        self.assertEqual(
            surveillance[
                "diseases"
            ][0]["disease_name"],
            "Demam Berdarah Dengue",
        )

        self.assertIn(
            "DBD",
            surveillance[
                "diseases"
            ][0]["matched_terms"],
        )

    @patch(
        "apps.crawlers.real_crawler."
        "parse_article_html"
    )
    def test_accepts_article_with_disease_name(
        self,
        mock_parse_article_html,
    ):
        mock_parse_article_html.return_value = (
            SimpleNamespace(
                title=(
                    "Demam Berdarah Dengue "
                    "meningkat"
                ),
                content=(
                    "Kasus Demam Berdarah Dengue "
                    "dilaporkan meningkat di wilayah "
                    "tersebut. Dinas Kesehatan masih "
                    "melakukan pemantauan dan tindakan "
                    "pengendalian terhadap kejadian."
                ),
                published_at=datetime(
                    2026,
                    7,
                    29,
                    8,
                    0,
                ),
                author="Redaksi",
                canonical_url=(
                    "https://example.com/"
                    "read/demam-berdarah"
                ),
                metadata={},
            )
        )

        client = self.build_mock_client(
            final_url=(
                "https://example.com/"
                "read/demam-berdarah"
            ),
        )

        payload = self.crawler._parse_article(
            source=self.source,
            client=client,
            article_url=(
                "https://example.com/"
                "read/demam-berdarah"
            ),
        )

        self.assertIsNotNone(
            payload
        )

        disease_names = [
            item["disease_name"]
            for item in payload.metadata[
                "surveillance"
            ]["diseases"]
        ]

        self.assertIn(
            "Demam Berdarah Dengue",
            disease_names,
        )

    @patch(
        "apps.crawlers.real_crawler."
        "parse_article_html"
    )
    def test_rejects_unrelated_article(
        self,
        mock_parse_article_html,
    ):
        mock_parse_article_html.return_value = (
            SimpleNamespace(
                title="Pembangunan jalan baru",
                content=(
                    "Pemerintah membangun jalan baru "
                    "untuk meningkatkan konektivitas "
                    "antarwilayah. Pembangunan tersebut "
                    "diharapkan dapat mendukung kegiatan "
                    "ekonomi dan memperlancar transportasi "
                    "masyarakat."
                ),
                published_at=datetime(
                    2026,
                    7,
                    29,
                    8,
                    0,
                ),
                author="Redaksi",
                canonical_url=(
                    "https://example.com/"
                    "read/pembangunan-jalan"
                ),
                metadata={},
            )
        )

        client = self.build_mock_client(
            final_url=(
                "https://example.com/"
                "read/pembangunan-jalan"
            ),
        )

        payload = self.crawler._parse_article(
            source=self.source,
            client=client,
            article_url=(
                "https://example.com/"
                "read/pembangunan-jalan"
            ),
        )

        self.assertIsNone(
            payload
        )

    @patch(
        "apps.crawlers.real_crawler."
        "parse_article_html"
    )
    def test_rejects_article_when_program_inactive(
        self,
        mock_parse_article_html,
    ):
        self.program.status = (
            SurveillanceProgram.Status.HISTORICAL
        )

        self.program.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

        mock_parse_article_html.return_value = (
            SimpleNamespace(
                title="Kasus DBD meningkat",
                content=(
                    "Dinas Kesehatan mencatat "
                    "peningkatan kasus DBD. "
                    "Pemantauan terhadap kejadian "
                    "masih dilakukan oleh petugas "
                    "kesehatan di wilayah tersebut."
                ),
                published_at=datetime(
                    2026,
                    7,
                    29,
                    8,
                    0,
                ),
                author="Redaksi",
                canonical_url=(
                    "https://example.com/"
                    "read/dbd-inactive"
                ),
                metadata={},
            )
        )

        client = self.build_mock_client(
            final_url=(
                "https://example.com/"
                "read/dbd-inactive"
            ),
        )

        payload = self.crawler._parse_article(
            source=self.source,
            client=client,
            article_url=(
                "https://example.com/"
                "read/dbd-inactive"
            ),
        )

        self.assertIsNone(
            payload
        )

    @patch(
        "apps.crawlers.real_crawler."
        "parse_article_html"
    )
    def test_rejects_article_with_short_content(
        self,
        mock_parse_article_html,
    ):
        mock_parse_article_html.return_value = (
            SimpleNamespace(
                title="Kasus DBD",
                content="Kasus DBD meningkat.",
                published_at=None,
                author="",
                canonical_url=(
                    "https://example.com/"
                    "read/dbd-pendek"
                ),
                metadata={},
            )
        )

        client = self.build_mock_client(
            final_url=(
                "https://example.com/"
                "read/dbd-pendek"
            ),
        )

        payload = self.crawler._parse_article(
            source=self.source,
            client=client,
            article_url=(
                "https://example.com/"
                "read/dbd-pendek"
            ),
        )

        self.assertIsNone(
            payload
        )