from datetime import datetime
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

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
from apps.locations.models import Location
from apps.sources.models import (
    Source,
    SourceSeedUrl,
    SourceUrlPattern,
)
from apps.sources.services import check_source_seed_readiness
from apps.ingestion.dto import ArticlePayload

from .base import BaseCrawler
from .implementations.static import (
    StaticTestCrawler,
)
from .real_crawler import (
    GenericHtmlCrawler,
)
from .rss_crawler import OfficialRssCrawler
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


class OfficialRssCrawlerTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media RSS Resmi Uji",
            code="media-rss-resmi-uji",
            domain="example.com",
            base_url="https://example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
            crawl_enabled=True,
            # Tetap HTML untuk membuktikan satu Source dapat memakai
            # kanal HTML dan RSS tanpa nilai strategi HYBRID.
            crawl_strategy=Source.CrawlStrategy.HTML,
            request_delay_seconds=0,
        )
        SourceUrlPattern.objects.create(
            source=self.source,
            pattern="/read/",
            pattern_type=SourceUrlPattern.PatternType.ALLOW,
            match_type=SourceUrlPattern.MatchType.PREFIX,
        )
        self.seed = SourceSeedUrl.objects.create(
            source=self.source,
            url="https://example.com/feed/health.xml",
            seed_type=SourceSeedUrl.SeedType.RSS,
            is_active=True,
        )
        self.user = get_user_model().objects.create_superuser(
            username="rss-admin",
            email="rss-admin@example.com",
            password="test-password",
        )

    @staticmethod
    def _feed(*links: str) -> str:
        items = "".join(
            (
                "<item>"
                f"<title>Berita kesehatan {index}</title>"
                f"<link>{link}</link>"
                f"<guid>rss-{index}</guid>"
                "</item>"
            )
            for index, link in enumerate(links, start=1)
        )
        return (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<rss version='2.0'><channel>"
            "<title>Feed Kesehatan</title>"
            f"{items}</channel></rss>"
        )

    @staticmethod
    def _feed_with_full_content(link: str) -> str:
        content = (
            "Dinas Kesehatan Kabupaten Bandung mencatat 42 kasus DBD "
            "di Kabupaten Bandung. Pemantauan epidemiologi dan upaya "
            "pengendalian penyakit dilakukan oleh petugas kesehatan "
            "bersama fasilitas pelayanan kesehatan setempat."
        )
        return (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<rss version='2.0' "
            "xmlns:content='http://purl.org/rss/1.0/modules/content/'>"
            "<channel><title>Feed Kesehatan</title><item>"
            "<title>Kasus DBD meningkat di Kabupaten Bandung</title>"
            f"<link>{link}</link><guid>rss-full-1</guid>"
            f"<content:encoded><![CDATA[<p>{content}</p>]]>"
            "</content:encoded>"
            "</item></channel></rss>"
        )

    @staticmethod
    def _payload(url: str) -> ArticlePayload:
        return ArticlePayload(
            source_code="media-rss-resmi-uji",
            url=url,
            title="Kasus DBD meningkat di Kabupaten Bandung",
            content=(
                "Dinas Kesehatan Kabupaten Bandung mencatat 42 kasus "
                "DBD di Kabupaten Bandung. Pemantauan epidemiologi dan "
                "pengendalian penyakit dilakukan oleh petugas kesehatan."
            ),
            metadata={"parser": "test"},
        )

    def _mock_feed_client(self, mock_client, feed_text: str):
        client = mock_client.return_value.__enter__.return_value
        client.get_feed.return_value = SimpleNamespace(
            text=feed_text,
            final_url=self.seed.url,
        )
        return client

    def test_html_primary_strategy_can_still_run_official_rss(self):
        readiness = check_source_seed_readiness(
            self.source,
            seed_types=(SourceSeedUrl.SeedType.RSS,),
        )

        self.assertTrue(readiness.is_ready)

    @patch("apps.crawlers.real_crawler.CrawlerHttpClient")
    @patch.object(GenericHtmlCrawler, "_crawl_listing_seed")
    def test_html_channel_does_not_dispatch_rss_seed(
        self,
        mock_crawl_listing_seed,
        _mock_client,
    ):
        listing_seed = SourceSeedUrl.objects.create(
            source=self.source,
            url="https://example.com/health",
            seed_type=SourceSeedUrl.SeedType.LISTING,
            is_active=True,
        )
        mock_crawl_listing_seed.return_value = iter(())

        list(GenericHtmlCrawler(source_code=self.source.code).crawl())

        self.assertEqual(mock_crawl_listing_seed.call_count, 1)
        dispatched_seed = mock_crawl_listing_seed.call_args.kwargs["seed"]
        self.assertEqual(dispatched_seed.id, listing_seed.id)

    @patch("apps.entities.services.exploit_article")
    @patch("apps.crawlers.rss_crawler.CrawlerHttpClient")
    @patch.object(OfficialRssCrawler, "_parse_article")
    def test_rss_job_uses_source_whitelist_and_records_provenance(
        self,
        mock_parse_article,
        mock_client,
        _mock_exploit_article,
    ):
        article_url = "https://example.com/read/kasus-dbd"
        self._mock_feed_client(
            mock_client,
            self._feed(article_url),
        )
        mock_parse_article.return_value = self._payload(article_url)

        result = run_crawler(
            OfficialRssCrawler(
                source_code=self.source.code,
                limit=5,
                candidate_limit=10,
            ),
            trigger_type="test",
        )

        self.assertEqual(result.total_created, 1)
        job = CollectionJob.objects.get(id=result.job_id)
        self.assertEqual(job.job_type, CollectionJob.JobType.RSS)
        self.assertEqual(
            job.metadata["collection_channel"],
            "official_rss",
        )
        article = Article.objects.get()
        self.assertEqual(
            article.raw_metadata["discovery_channel"],
            "official_rss",
        )
        self.assertEqual(
            article.raw_metadata["retrieval_channel"],
            "publisher_html",
        )
        self.assertEqual(
            article.raw_metadata["rss"]["feed_url"],
            self.seed.url,
        )

    @patch("apps.entities.services.exploit_article")
    @patch("apps.crawlers.rss_crawler.CrawlerHttpClient")
    @patch.object(OfficialRssCrawler, "_parse_article")
    def test_official_full_text_feed_does_not_fetch_article_html(
        self,
        mock_parse_article,
        mock_client,
        _mock_exploit_article,
    ):
        article_url = "https://example.com/read/kasus-dbd-full-rss"
        self._mock_feed_client(
            mock_client,
            self._feed_with_full_content(article_url),
        )

        result = run_crawler(
            OfficialRssCrawler(source_code=self.source.code),
            trigger_type="test",
        )

        self.assertEqual(result.total_created, 1)
        mock_parse_article.assert_not_called()
        article = Article.objects.get()
        self.assertEqual(
            article.raw_metadata["retrieval_channel"],
            "official_rss_full_content",
        )
        item = CollectionJobItem.objects.get()
        self.assertEqual(
            item.metadata["stage"],
            "accepted_from_rss_full_content",
        )

    @patch("apps.crawlers.rss_crawler.CrawlerHttpClient")
    @patch.object(OfficialRssCrawler, "_parse_article")
    def test_rss_rejects_entry_from_unregistered_domain(
        self,
        mock_parse_article,
        mock_client,
    ):
        self._mock_feed_client(
            mock_client,
            self._feed("https://unlisted.example.org/read/kasus-dbd"),
        )

        result = run_crawler(
            OfficialRssCrawler(source_code=self.source.code),
            trigger_type="test",
        )

        self.assertEqual(result.total_rejected, 1)
        self.assertEqual(result.total_created, 0)
        mock_parse_article.assert_not_called()
        item = CollectionJobItem.objects.get()
        self.assertEqual(
            item.metadata["stage"],
            "rss_entry_validation",
        )

    @patch("apps.entities.services.exploit_article")
    @patch("apps.crawlers.rss_crawler.CrawlerHttpClient")
    @patch.object(OfficialRssCrawler, "_parse_article")
    def test_rss_deduplicates_repeated_entry_before_second_fetch(
        self,
        mock_parse_article,
        mock_client,
        _mock_exploit_article,
    ):
        article_url = "https://example.com/read/kasus-dbd"
        self._mock_feed_client(
            mock_client,
            self._feed(article_url, article_url),
        )
        mock_parse_article.return_value = self._payload(article_url)

        result = run_crawler(
            OfficialRssCrawler(source_code=self.source.code),
            trigger_type="test",
        )

        self.assertEqual(result.total_created, 1)
        self.assertEqual(result.total_duplicate, 1)
        self.assertEqual(mock_parse_article.call_count, 1)

    @patch("apps.crawlers.rss_crawler.CrawlerHttpClient")
    def test_malformed_feed_is_audited_as_failed(self, mock_client):
        self._mock_feed_client(
            mock_client,
            "not a feed",
        )

        result = run_crawler(
            OfficialRssCrawler(source_code=self.source.code),
            trigger_type="test",
        )

        self.assertEqual(result.total_failed, 1)
        job = CollectionJob.objects.get(id=result.job_id)
        self.assertEqual(job.status, CollectionJob.Status.FAILED)
        self.assertEqual(
            job.items.get().metadata["stage"],
            "rss_feed_parsing",
        )

    def test_management_command_lists_ready_rss_source(self):
        output = StringIO()

        call_command(
            "run_rss_crawler",
            "--list-ready",
            stdout=output,
        )

        self.assertIn(self.source.code, output.getvalue())

    def test_collection_page_exposes_official_rss_channel(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard:crawler-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jalankan RSS Resmi")
        self.assertContains(response, self.source.code)

    @patch("apps.dashboard.views.run_crawler_in_background")
    def test_collection_endpoint_starts_official_rss_crawler(
        self,
        mock_run_in_background,
    ):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("dashboard:crawler-run"),
            {
                "channel": "rss",
                "source": self.source.code,
                "limit": "5",
                "candidate_limit": "10",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["job_type"], "rss")
        crawler = mock_run_in_background.call_args.args[0]
        self.assertIsInstance(crawler, OfficialRssCrawler)
        self.assertEqual(crawler.source_code, self.source.code)


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
