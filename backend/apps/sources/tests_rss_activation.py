from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.crawlers.http_client import HttpPage

from .models import Source, SourceSeedUrl, SourceUrlPattern
from .rss_audit import collect_feed_candidates, discover_feed_urls


class OfficialRssActivationTests(TestCase):
    feed_url = "https://example.com/rss.xml"

    def setUp(self):
        self.source = Source.objects.create(
            name="Media RSS Uji",
            code="media-rss-uji",
            domain="example.com",
            base_url="https://example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
            crawl_enabled=False,
            request_delay_seconds=0,
        )
        SourceUrlPattern.objects.create(
            source=self.source,
            pattern="/news/",
            pattern_type=SourceUrlPattern.PatternType.ALLOW,
            match_type=SourceUrlPattern.MatchType.PREFIX,
        )

    @staticmethod
    def _feed(*urls: str) -> str:
        items = "".join(
            (
                "<item>"
                f"<title>Berita kesehatan {index}</title>"
                f"<link>{url}</link>"
                f"<guid>rss-{index}</guid>"
                "</item>"
            )
            for index, url in enumerate(urls, start=1)
        )
        return (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<rss version='2.0'><channel><title>RSS Uji</title>"
            f"{items}</channel></rss>"
        )

    def _feed_page(self, *urls: str) -> HttpPage:
        return HttpPage(
            requested_url=self.feed_url,
            final_url=self.feed_url,
            status_code=200,
            content_type="application/rss+xml",
            text=self._feed(*urls),
        )

    @patch("apps.sources.rss_audit.CrawlerHttpClient.get_feed")
    def test_dry_run_does_not_change_database(self, mock_get_feed):
        mock_get_feed.return_value = self._feed_page(
            "https://example.com/news/dbd-meningkat"
        )
        output = StringIO()

        call_command(
            "activate_official_rss",
            "--source",
            self.source.code,
            "--feed-url",
            self.feed_url,
            "--no-html-discovery",
            stdout=output,
        )

        self.source.refresh_from_db()
        self.assertFalse(self.source.crawl_enabled)
        self.assertFalse(SourceSeedUrl.objects.exists())
        self.assertIn("DRY-RUN", output.getvalue())

    @patch("apps.sources.rss_audit.CrawlerHttpClient.get_feed")
    def test_apply_activates_only_valid_feed(self, mock_get_feed):
        mock_get_feed.return_value = self._feed_page(
            "https://example.com/news/dbd-meningkat",
            "https://example.com/news/malaria-meningkat",
        )

        call_command(
            "activate_official_rss",
            "--source",
            self.source.code,
            "--feed-url",
            self.feed_url,
            "--no-html-discovery",
            "--apply",
            stdout=StringIO(),
        )

        self.source.refresh_from_db()
        seed = SourceSeedUrl.objects.get(source=self.source)
        self.assertTrue(self.source.crawl_enabled)
        self.assertEqual(seed.url, self.feed_url)
        self.assertEqual(seed.seed_type, SourceSeedUrl.SeedType.RSS)
        self.assertTrue(seed.is_active)

    @patch("apps.sources.rss_audit.CrawlerHttpClient.get_feed")
    def test_apply_is_idempotent(self, mock_get_feed):
        mock_get_feed.return_value = self._feed_page(
            "https://example.com/news/dbd-meningkat"
        )

        for _ in range(2):
            call_command(
                "activate_official_rss",
                "--source",
                self.source.code,
                "--feed-url",
                self.feed_url,
                "--no-html-discovery",
                "--apply",
                stdout=StringIO(),
            )

        self.assertEqual(SourceSeedUrl.objects.count(), 1)

    @patch("apps.sources.rss_audit.CrawlerHttpClient.get_feed")
    def test_feed_is_rejected_when_article_urls_fail_policy(
        self,
        mock_get_feed,
    ):
        mock_get_feed.return_value = self._feed_page(
            "https://example.com/video/dbd",
            "https://outside.example/news/dbd",
        )
        output = StringIO()

        call_command(
            "activate_official_rss",
            "--source",
            self.source.code,
            "--feed-url",
            self.feed_url,
            "--no-html-discovery",
            "--apply",
            stdout=output,
        )

        self.source.refresh_from_db()
        self.assertFalse(self.source.crawl_enabled)
        self.assertFalse(SourceSeedUrl.objects.exists())
        self.assertIn("REJECT", output.getvalue())

    @patch("apps.sources.rss_audit.CrawlerHttpClient.get_feed")
    def test_unverified_source_is_not_fetched_or_activated(
        self,
        mock_get_feed,
    ):
        self.source.is_verified = False
        self.source.save()

        call_command(
            "activate_official_rss",
            "--source",
            self.source.code,
            "--feed-url",
            self.feed_url,
            "--no-html-discovery",
            "--apply",
            stdout=StringIO(),
        )

        mock_get_feed.assert_not_called()
        self.source.refresh_from_db()
        self.assertFalse(self.source.crawl_enabled)
        self.assertFalse(SourceSeedUrl.objects.exists())

    def test_html_autodiscovery_accepts_only_same_source_domain(self):
        client = type(
            "Client",
            (),
            {
                "get_html": lambda _self, _url: HttpPage(
                    requested_url="https://example.com",
                    final_url="https://example.com",
                    status_code=200,
                    content_type="text/html",
                    text=(
                        "<html><head>"
                        "<link rel='alternate' type='application/rss+xml' "
                        "href='/feed.xml'>"
                        "<link rel='alternate' type='application/rss+xml' "
                        "href='https://outside.example/feed.xml'>"
                        "</head></html>"
                    ),
                )
            },
        )()

        self.assertEqual(
            discover_feed_urls(self.source, client),
            ("https://example.com/feed.xml",),
        )

    def test_existing_rss_seed_is_always_reaudited(self):
        SourceSeedUrl.objects.create(
            source=self.source,
            url=self.feed_url,
            seed_type=SourceSeedUrl.SeedType.RSS,
            is_active=False,
        )
        client = type(
            "Client",
            (),
            {"get_html": lambda _self, _url: None},
        )()

        candidates = collect_feed_candidates(
            self.source,
            client,
            discover_from_html=False,
        )

        self.assertIn(self.feed_url, candidates.urls)
