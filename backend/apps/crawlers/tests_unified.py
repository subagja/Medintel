from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.collection.models import CollectionJob, CollectionSession
from apps.sources.models import Source, SourceSeedUrl, SourceUrlPattern

from .base import BaseCrawler
from .services import run_crawler
from .unified import (
    build_unified_collection_plan,
    start_unified_collection,
)


class EmptyUnifiedCrawler(BaseCrawler):
    source_code = "media-html"

    def crawl(self):
        yield from ()


class UnifiedCollectionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="unified-admin",
            email="unified@example.com",
            password="test-password",
        )
        self.rss_source = self._source(
            name="Media RSS dan HTML",
            code="media-rss-html",
            domain="rss-html.example.com",
        )
        self._seed(
            self.rss_source,
            "https://rss-html.example.com/kesehatan",
            SourceSeedUrl.SeedType.LISTING,
        )
        self._seed(
            self.rss_source,
            "https://rss-html.example.com/feed.xml",
            SourceSeedUrl.SeedType.RSS,
        )

        self.html_source = self._source(
            name="Media HTML",
            code="media-html",
            domain="html.example.com",
        )
        self._seed(
            self.html_source,
            "https://html.example.com/kesehatan",
            SourceSeedUrl.SeedType.LISTING,
        )

    @staticmethod
    def _source(*, name: str, code: str, domain: str) -> Source:
        source = Source.objects.create(
            name=name,
            code=code,
            domain=domain,
            base_url=f"https://{domain}",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
            crawl_enabled=True,
            request_delay_seconds=0,
        )
        SourceUrlPattern.objects.create(
            source=source,
            pattern="/",
            pattern_type=SourceUrlPattern.PatternType.ALLOW,
            match_type=SourceUrlPattern.MatchType.PREFIX,
        )
        return source

    @staticmethod
    def _seed(source: Source, url: str, seed_type: str) -> None:
        SourceSeedUrl.objects.create(
            source=source,
            url=url,
            seed_type=seed_type,
            is_active=True,
        )

    def test_plan_prefers_rss_and_uses_html_as_fallback(self):
        plan = build_unified_collection_plan(
            source_code="all",
            include_google_news=False,
            html_deep_scan=False,
        )

        pairs = {(spec.source_code, spec.job_type) for spec in plan.specs}
        self.assertIn(
            (self.rss_source.code, CollectionJob.JobType.RSS),
            pairs,
        )
        self.assertNotIn(
            (self.rss_source.code, CollectionJob.JobType.CRAWLER),
            pairs,
        )
        self.assertIn(
            (self.html_source.code, CollectionJob.JobType.CRAWLER),
            pairs,
        )

    def test_deep_scan_adds_html_without_replacing_rss(self):
        plan = build_unified_collection_plan(
            source_code=self.rss_source.code,
            include_google_news=False,
            html_deep_scan=True,
        )

        self.assertEqual(
            {spec.job_type for spec in plan.specs},
            {CollectionJob.JobType.RSS, CollectionJob.JobType.CRAWLER},
        )

    @patch(
        "apps.crawlers.unified.build_google_news_disease_scope",
        return_value=SimpleNamespace(batches=(object(),)),
    )
    def test_google_news_whitelist_follows_selected_source_scope(self, _mock):
        plan = build_unified_collection_plan(
            source_code=self.html_source.code,
            include_google_news=True,
            html_deep_scan=False,
        )

        self.assertEqual(
            plan.google_news_source_codes,
            (self.html_source.code,),
        )
        self.assertIn(
            CollectionJob.JobType.GOOGLE_NEWS,
            {spec.job_type for spec in plan.specs},
        )

    def test_start_creates_one_session_with_pending_channel_jobs(
        self,
    ):
        session = start_unified_collection(
            source_code="all",
            include_google_news=False,
            html_deep_scan=False,
            article_limit=10,
            candidate_limit=30,
            triggered_by=self.user,
        )

        self.assertEqual(CollectionSession.objects.count(), 1)
        self.assertEqual(session.planned_job_count, 2)
        self.assertEqual(session.jobs.count(), 2)
        self.assertFalse(
            session.jobs.exclude(status=CollectionJob.Status.PENDING).exists()
        )
        self.assertEqual(
            session.metadata["policy"],
            "rss_primary_html_fallback",
        )
        self.assertTrue(session.jobs.exclude(queue_key="").exists())

    def test_running_channel_is_skipped_instead_of_duplicated(self):
        CollectionJob.objects.create(
            source=self.rss_source,
            job_type=CollectionJob.JobType.RSS,
            status=CollectionJob.Status.RUNNING,
        )

        plan = build_unified_collection_plan(
            source_code="all",
            include_google_news=False,
            html_deep_scan=False,
        )

        self.assertNotIn(
            self.rss_source.code,
            {spec.source_code for spec in plan.specs},
        )
        self.assertTrue(
            any(
                item["source_code"] == self.rss_source.code
                for item in plan.skipped
            )
        )

    def test_runner_activates_existing_planned_job(self):
        session = CollectionSession.objects.create(
            scope=CollectionSession.Scope.SINGLE_SOURCE,
            selected_source=self.html_source,
            planned_job_count=1,
        )
        planned_job = CollectionJob.objects.create(
            session=session,
            source=self.html_source,
            job_type=CollectionJob.JobType.CRAWLER,
            status=CollectionJob.Status.PENDING,
        )

        result = run_crawler(
            EmptyUnifiedCrawler(),
            session=session,
            existing_job_id=planned_job.id,
            trigger_type="test",
        )

        planned_job.refresh_from_db()
        self.assertEqual(CollectionJob.objects.count(), 1)
        self.assertEqual(result.job_id, str(planned_job.id))
        self.assertEqual(planned_job.status, CollectionJob.Status.COMPLETED)

    def test_collection_page_presents_unified_flow_as_primary(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard:crawler-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jalankan Koleksi Terpadu")
        self.assertContains(response, "Alat Manual dan Diagnostik Kanal")
        self.assertContains(response, "Jalankan RSS Resmi")
        self.assertContains(response, "Maks. artikel diproses")
        self.assertContains(response, "Maks. kandidat diperiksa")
        self.assertContains(response, "Cara kerja batas koleksi")
        self.assertContains(
            response,
            "bukan jaminan jumlah artikel baru",
        )
        self.assertContains(
            response,
            'placeholder="Kosong = default 100"',
            count=4,
            html=False,
        )
        self.assertNotContains(response, "Crawler Artikel/Web")

    def test_unified_endpoint_returns_session_detail_url(
        self,
    ):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("dashboard:crawler-unified-run"),
            {
                "source": "all",
                "limit": "10",
                "candidate_limit": "30",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["planned_job_count"], 2)
        self.assertIn("/crawler-artikel/sesi/", payload["detail_url"])

    def test_session_detail_aggregates_child_jobs(
        self,
    ):
        session = start_unified_collection(
            source_code="all",
            include_google_news=False,
            html_deep_scan=False,
            article_limit=None,
            candidate_limit=None,
            triggered_by=self.user,
        )
        first_job = session.jobs.first()
        first_job.status = CollectionJob.Status.COMPLETED
        first_job.total_found = 4
        first_job.total_created = 1
        first_job.total_rejected = 2
        first_job.save()

        self.client.force_login(self.user)
        response = self.client.get(
            reverse(
                "dashboard:crawler-session-detail",
                kwargs={"session_id": session.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, session.reference)
        self.assertContains(response, "Proses Kanal dalam Sesi")
        self.assertEqual(response.context["session_totals"]["total_created"], 1)
