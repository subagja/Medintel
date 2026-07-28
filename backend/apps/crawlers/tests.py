from django.test import TestCase

from apps.articles.models import Article
from apps.sources.models import Source, SourceUrlPattern
from apps.collection.models import (
    CollectionJob,
    CollectionJobItem,
)

from .implementations.static import StaticTestCrawler
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