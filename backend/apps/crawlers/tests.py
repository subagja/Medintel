from django.test import TestCase

from apps.articles.models import Article
from apps.sources.models import Source, SourceUrlPattern

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