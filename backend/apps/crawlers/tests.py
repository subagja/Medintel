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
    Location,
    SurveillanceDisease,
    SurveillanceProgram,
)
from apps.sources.models import (
    Source,
    SourceUrlPattern,
)

from .base import BaseCrawler
from .implementations.static import (
    StaticTestCrawler,
)
from .real_crawler import (
    GenericHtmlCrawler,
)
from .services import run_crawler
from .results import (
    CrawlItemEvent,
    CrawlItemStatus,
)


class EventRecordingTestCrawler(BaseCrawler):
    source_code = "media-uji"

    def crawl(self):
        self.emit_item_event(
            CrawlItemEvent(
                original_url=(
                    "https://example.com/read/tidak-relevan"
                ),
                normalized_url=(
                    "https://example.com/read/tidak-relevan"
                ),
                title="Artikel nonkesehatan",
                status=CrawlItemStatus.REJECTED,
                reason="Tidak lolos filter murah.",
                metadata={
                    "stage": "cheap_filter",
                },
            )
        )
        self.emit_item_event(
            CrawlItemEvent(
                original_url=(
                    "https://example.com/read/gagal-diunduh"
                ),
                normalized_url=(
                    "https://example.com/read/gagal-diunduh"
                ),
                status=CrawlItemStatus.FAILED,
                reason="Halaman artikel gagal diambil.",
                error_message="HTTP 503",
                metadata={
                    "stage": "article_download",
                },
            )
        )

        yield from ()


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

    def test_crawler_records_rejection_and_failure_reasons(self):
        result = run_crawler(
            EventRecordingTestCrawler(),
            trigger_type="test",
        )

        self.assertEqual(result.total_found, 2)
        self.assertEqual(result.total_rejected, 1)
        self.assertEqual(result.total_failed, 1)
        self.assertIsNotNone(result.job_id)

        job = CollectionJob.objects.get(
            id=result.job_id,
        )

        self.assertEqual(
            job.status,
            CollectionJob.Status.COMPLETED_WITH_ERRORS,
        )
        self.assertEqual(job.items.count(), 2)

        rejected_item = job.items.get(
            status=CollectionJobItem.Status.REJECTED,
        )
        failed_item = job.items.get(
            status=CollectionJobItem.Status.FAILED,
        )

        self.assertEqual(
            rejected_item.metadata["stage"],
            "cheap_filter",
        )
        self.assertEqual(
            failed_item.error_message,
            "HTTP 503",
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

        self.disease, _ = Disease.objects.get_or_create(
            name="Demam Berdarah Dengue",
            defaults={
                "canonical_name": "Dengue",
                "code": "dbd-crawler-test",
                "category": "Penyakit Menular",
                "is_priority": True,
                "is_active": True,
            },
        )

        Disease.objects.filter(
            pk=self.disease.pk,
        ).update(
            is_active=True,
        )

        self.disease.refresh_from_db()

        disease_alias = DiseaseAlias.objects.filter(
            disease=self.disease,
            alias__iexact="DBD",
        ).first()

        if disease_alias is None:
            DiseaseAlias.objects.create(
                disease=self.disease,
                alias="DBD",
                language="id",
                is_active=True,
            )
        elif not disease_alias.is_active:
            disease_alias.is_active = True
            disease_alias.save(
                update_fields=["is_active"],
            )

        # Mengisolasi pengujian dari program surveilans bawaan migration.
        SurveillanceDisease.objects.filter(
            disease=self.disease,
        ).update(
            is_active=False,
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

        self.location, _ = Location.objects.get_or_create(
            name="Kabupaten Bandung",
            defaults={
                "administrative_level": (
                    Location.AdministrativeLevel.REGENCY
                ),
                "country_code": "ID",
                "is_active": True,
            },
        )

        if not self.location.is_active:
            self.location.is_active = True
            self.location.save(
                update_fields=["is_active"],
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
                    "Dinas Kesehatan Kabupaten Bandung mencatat "
                    "42 kasus DBD di Kabupaten Bandung. "
                    "Dua pasien meninggal dunia dan 5 pasien "
                    "masih dirawat. Pemantauan dan tindakan "
                    "pengendalian dilakukan oleh petugas "
                    "kesehatan setempat. Masyarakat diminta "
                    "menjaga kebersihan lingkungan."
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
            "dbd",
            [
                term.casefold()
                for term in surveillance[
                    "diseases"
                ][0]["matched_terms"]
            ],
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
                    "Dinas Kesehatan Kabupaten Bandung mencatat "
                    "31 kasus Demam Berdarah Dengue di Kabupaten "
                    "Bandung. Satu pasien meninggal dunia dan "
                    "7 pasien masih dirawat. Pemantauan serta "
                    "tindakan pengendalian terhadap kejadian "
                    "masih dilakukan oleh petugas kesehatan."
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
    def test_accepts_word_number_for_death(
        self,
        mock_parse_article_html,
    ):
        mock_parse_article_html.return_value = (
            SimpleNamespace(
                title="Dua kematian akibat DBD",
                content=(
                    "Dinas Kesehatan Kabupaten Bandung "
                    "melaporkan dua kematian akibat DBD di "
                    "Kabupaten Bandung. Petugas kesehatan "
                    "melakukan penyelidikan epidemiologi dan "
                    "pengendalian vektor di wilayah terdampak. "
                    "Masyarakat diminta segera memeriksakan diri "
                    "apabila mengalami gejala."
                ),
                published_at=datetime(
                    2026,
                    8,
                    4,
                    8,
                    0,
                ),
                author="Redaksi Kesehatan",
                canonical_url=(
                    "https://example.com/"
                    "read/dua-kematian-dbd"
                ),
                metadata={},
            )
        )

        client = self.build_mock_client(
            final_url=(
                "https://example.com/"
                "read/dua-kematian-dbd"
            ),
        )

        payload = self.crawler._parse_article(
            source=self.source,
            client=client,
            article_url=(
                "https://example.com/"
                "read/dua-kematian-dbd"
            ),
        )

        self.assertIsNotNone(payload)

        counts = payload.metadata[
            "surveillance"
        ]["counts"]

        self.assertIn(
            {
                "value": 2,
                "matched_text": "dua kematian",
                "metric_type": "death",
            },
            counts,
        )

    @patch(
        "apps.crawlers.real_crawler."
        "parse_article_html"
    )
    def test_accepts_indonesian_thousands_for_suspects(
        self,
        mock_parse_article_html,
    ):
        mock_parse_article_html.return_value = (
            SimpleNamespace(
                title="Ribuan suspek DBD dipantau",
                content=(
                    "Dinas Kesehatan Kabupaten Bandung mencatat "
                    "13.297 suspek DBD di Kabupaten Bandung. "
                    "Data tersebut masih berstatus dugaan dan "
                    "tidak boleh diperlakukan sebagai kasus "
                    "terkonfirmasi. Pemeriksaan lanjutan serta "
                    "pemantauan epidemiologi masih dilakukan oleh "
                    "petugas kesehatan."
                ),
                published_at=datetime(
                    2026,
                    8,
                    4,
                    8,
                    0,
                ),
                author="Redaksi Kesehatan",
                canonical_url=(
                    "https://example.com/"
                    "read/suspek-dbd"
                ),
                metadata={},
            )
        )

        client = self.build_mock_client(
            final_url=(
                "https://example.com/"
                "read/suspek-dbd"
            ),
        )

        payload = self.crawler._parse_article(
            source=self.source,
            client=client,
            article_url=(
                "https://example.com/"
                "read/suspek-dbd"
            ),
        )

        self.assertIsNotNone(payload)

        counts = payload.metadata[
            "surveillance"
        ]["counts"]

        self.assertIn(
            {
                "value": 13297,
                "matched_text": "13.297 suspek",
                "metric_type": "suspect",
            },
            counts,
        )
        self.assertNotIn(
            "confirmed_case",
            [item["metric_type"] for item in counts],
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
