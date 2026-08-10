import json
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.articles.models import Article
from apps.collection.models import CollectionJob, CollectionJobItem
from apps.entities.models import (
    Disease,
    DiseaseAlias,
    SurveillanceDisease,
    SurveillanceProgram,
)
from apps.ingestion.dto import ArticlePayload
from apps.sources.models import Source, SourceUrlPattern

from .google_news_crawler import (
    GoogleNewsRssCrawler,
    check_google_news_publisher_readiness,
    get_google_news_allowed_sources,
)
from .google_news_queries import build_google_news_disease_scope
from .google_news_resolver import (
    decode_legacy_google_news_url,
    extract_publisher_url_from_rpc_response,
)
from .http_client import CrawlerHttpClient, CrawlerHttpError, HttpPage
from .services import run_crawler


class GoogleNewsRssCrawlerTests(TestCase):
    def setUp(self):
        self.source = self._create_source(
            name="Media Google News Uji",
            code="media-google-news-uji",
            domain="example.com",
        )
        SurveillanceDisease.objects.update(is_active=False)
        self.program = SurveillanceProgram.objects.create(
            name="Program Discovery Google News Uji",
            code="program-discovery-google-news-uji",
            status=SurveillanceProgram.Status.ACTIVE,
        )
        self.disease = Disease.objects.get(name="Demam Berdarah Dengue")
        SurveillanceDisease.objects.create(
            program=self.program,
            disease=self.disease,
            official_name="Demam Berdarah Dengue",
            category="Penyakit Menular",
            is_active=True,
        )
        DiseaseAlias.objects.get_or_create(
            disease=self.disease,
            alias="DBD",
            defaults={"is_active": True},
        )
        self.query_batch = build_google_news_disease_scope().batches[0]
        self.user = get_user_model().objects.create_superuser(
            username="google-news-admin",
            email="google-news-admin@example.com",
            password="test-password",
        )

    @staticmethod
    def _create_source(*, name: str, code: str, domain: str) -> Source:
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
            pattern="/read/",
            pattern_type=SourceUrlPattern.PatternType.ALLOW,
            match_type=SourceUrlPattern.MatchType.PREFIX,
        )
        return source

    @staticmethod
    def _feed(
        *links: str,
        published: str | None = None,
        source_title: str = "Media Uji",
    ) -> str:
        items = "".join(
            (
                "<item>"
                f"<title>Kasus DBD meningkat {index}</title>"
                f"<link>{link}</link>"
                f"<guid>google-news-{index}</guid>"
                + (f"<pubDate>{published}</pubDate>" if published else "")
                + f"<source url='https://hint.invalid'>{source_title}</source>"
                "</item>"
            )
            for index, link in enumerate(links, start=1)
        )
        return (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<rss version='2.0'><channel>"
            "<title>Google News</title>"
            f"{items}</channel></rss>"
        )

    @staticmethod
    def _payload(url: str, source_code: str) -> ArticlePayload:
        return ArticlePayload(
            source_code=source_code,
            url=url,
            title="Kasus DBD meningkat di Kabupaten Bandung",
            content=(
                "Dinas Kesehatan Kabupaten Bandung mencatat 42 kasus DBD "
                "di Kabupaten Bandung. Pemantauan epidemiologi dan upaya "
                "pengendalian dilakukan petugas kesehatan setempat."
            ),
            metadata={"parser": "test"},
        )

    def _mock_client(
        self,
        mock_client,
        *,
        feed_text: str,
        final_url: str,
        publisher_error: Exception | None = None,
        interstitial_html: str = "",
    ):
        client = mock_client.return_value.__enter__.return_value
        feed_url = GoogleNewsRssCrawler.build_feed_url(
            query_text=self.query_batch.query,
        )
        client.get_discovery_feed.return_value = HttpPage(
            requested_url=feed_url,
            final_url=feed_url,
            status_code=200,
            content_type="application/rss+xml",
            text=feed_text,
        )
        client.resolve_discovery_url.return_value = HttpPage(
            requested_url="https://news.google.com/rss/articles/token",
            final_url=final_url,
            status_code=200 if "news.google.com" in final_url else 302,
            content_type="text/html; charset=utf-8",
            text=interstitial_html,
        )
        if publisher_error:
            client.get_html.side_effect = publisher_error
        else:
            client.get_html.return_value = HttpPage(
                requested_url=final_url,
                final_url=final_url,
                status_code=200,
                content_type="text/html; charset=utf-8",
                text="<html><body>publisher page</body></html>",
            )
        return client

    def test_feed_url_is_global_and_not_scoped_to_source(self):
        feed_url = GoogleNewsRssCrawler.build_feed_url(
            query_text=self.query_batch.query,
        )
        self.assertIn("news.google.com/rss/search", feed_url)
        self.assertNotIn("site%3A", feed_url)
        self.assertIn("ceid=ID%3Aid", feed_url)
        self.assertIn("DBD", feed_url)

    def test_discovery_feed_allowlist_does_not_apply_publisher_robots(self):
        feed_url = GoogleNewsRssCrawler.build_feed_url(
            query_text=self.query_batch.query,
        )
        client = CrawlerHttpClient(None)
        client._request = Mock(
            return_value=SimpleNamespace(
                status_code=200,
                encoding="utf-8",
                apparent_encoding="utf-8",
                headers={"Content-Type": "application/rss+xml"},
                text=self._feed(
                    "https://news.google.com/rss/articles/token"
                ),
                url=feed_url,
                is_redirect=False,
            )
        )
        client.is_allowed_by_robots = Mock(
            side_effect=AssertionError("robots Source tidak boleh dipakai")
        )
        page = client.get_discovery_feed(
            feed_url,
            allowed_hosts={"news.google.com"},
            allowed_path_prefixes=("/rss/search",),
        )
        self.assertEqual(page.final_url, feed_url)
        client.is_allowed_by_robots.assert_not_called()
        client.close()

    def test_discovery_resolver_stops_before_fetching_publisher(self):
        google_url = "https://news.google.com/rss/articles/token"
        publisher_url = "https://example.com/read/kasus-dbd"
        client = CrawlerHttpClient(None)
        client._request = Mock(
            return_value=SimpleNamespace(
                status_code=302,
                encoding="utf-8",
                apparent_encoding="utf-8",
                headers={"Location": publisher_url},
                text="",
                url=google_url,
                is_redirect=True,
            )
        )
        page = client.resolve_discovery_url(
            google_url,
            allowed_hosts={"news.google.com"},
            allowed_path_prefixes=("/rss/articles/",),
        )
        self.assertEqual(page.final_url, publisher_url)
        self.assertEqual(client._request.call_count, 1)
        self.assertFalse(client._request.call_args.kwargs["allow_redirects"])
        client.close()

    def test_legacy_google_news_token_is_decoded_locally(self):
        google_url = (
            "https://news.google.com/rss/articles/"
            "CBMiSGh0dHBzOi8vdGVjaGNydW5jaC5jb20vMjAyMi8xMC8yNy9uZXct"
            "eW9yay1wb3N0LWhhY2tlZC1vZmZlbnNpdmUtdHdlZXRzL9IBAA?oc=5"
        )
        self.assertEqual(
            decode_legacy_google_news_url(google_url),
            (
                "https://techcrunch.com/2022/10/27/"
                "new-york-post-hacked-offensive-tweets/"
            ),
        )

    def test_discovery_rpc_form_is_restricted_to_allowed_endpoint(self):
        rpc_url = (
            "https://news.google.com/_/DotsSplashUi/data/"
            "batchexecute?rpcids=Fbv4je"
        )
        client = CrawlerHttpClient(None)
        client._post_form = Mock(
            return_value=SimpleNamespace(
                status_code=200,
                encoding="utf-8",
                apparent_encoding="utf-8",
                headers={"Content-Type": "application/json"},
                text="[]",
                url=rpc_url,
                is_redirect=False,
            )
        )
        page = client.post_discovery_form(
            rpc_url,
            data={"f.req": "[]"},
            allowed_hosts={"news.google.com"},
            allowed_path_prefixes=(
                "/_/DotsSplashUi/data/batchexecute",
            ),
            referer="https://news.google.com/articles/token",
        )
        self.assertEqual(page.final_url, rpc_url)

        with self.assertRaises(CrawlerHttpError):
            client.post_discovery_form(
                "https://example.com/batchexecute",
                data={"f.req": "[]"},
                allowed_hosts={"news.google.com"},
                allowed_path_prefixes=(
                    "/_/DotsSplashUi/data/batchexecute",
                ),
                referer="https://news.google.com/articles/token",
            )
        self.assertEqual(client._post_form.call_count, 1)
        client.close()

    def test_discovery_feed_rejects_endpoint_outside_allowlist(self):
        client = CrawlerHttpClient(None)
        client._request = Mock()
        with self.assertRaises(CrawlerHttpError):
            client.get_discovery_feed(
                "https://example.com/rss/search?q=DBD",
                allowed_hosts={"news.google.com"},
                allowed_path_prefixes=("/rss/search",),
            )
        client._request.assert_not_called()
        client.close()

    def test_query_scope_uses_active_master_and_filters_short_alias(self):
        DiseaseAlias.objects.get_or_create(
            disease=self.disease,
            alias="AI",
            defaults={"is_active": True},
        )
        DiseaseAlias.objects.get_or_create(
            disease=self.disease,
            alias="Dengue Hemorrhagic Fever",
            defaults={"is_active": True},
        )
        scope = build_google_news_disease_scope()
        self.assertEqual(scope.disease_count, 1)
        self.assertIn("DBD", scope.batches[0].terms)
        self.assertIn("Dengue Hemorrhagic Fever", scope.batches[0].terms)
        self.assertNotIn("AI", scope.batches[0].terms)

    def test_feed_query_requests_the_configured_age_window(self):
        feed_url = GoogleNewsRssCrawler.build_feed_url(
            query_text="DBD OR malaria",
            max_age_days=7,
        )
        query = parse_qs(urlsplit(feed_url).query)["q"][0]
        self.assertEqual(query, "(DBD OR malaria) when:7d")

    def test_allowed_sources_are_derived_without_discovery_query(self):
        self.assertFalse(self.source.discovery_queries.exists())
        self.assertIn(self.source, get_google_news_allowed_sources())
        self.assertTrue(
            check_google_news_publisher_readiness(self.source).is_ready
        )

    @patch("apps.entities.services.exploit_article")
    @patch("apps.crawlers.google_news_crawler.CrawlerHttpClient")
    @patch.object(GoogleNewsRssCrawler, "_parse_article")
    def test_resolved_url_creates_article_and_global_job(
        self,
        mock_parse_article,
        mock_client,
        _mock_exploit_article,
    ):
        google_url = "https://news.google.com/rss/articles/token"
        publisher_url = "https://example.com/read/kasus-dbd"
        client = self._mock_client(
            mock_client,
            feed_text=self._feed(google_url, source_title="Penerbit Palsu"),
            final_url=publisher_url,
        )
        mock_parse_article.return_value = self._payload(
            publisher_url,
            self.source.code,
        )
        result = run_crawler(GoogleNewsRssCrawler(), trigger_type="test")

        self.assertEqual(result.total_created, 1)
        job = CollectionJob.objects.get(id=result.job_id)
        self.assertIsNone(job.source)
        self.assertEqual(job.job_type, CollectionJob.JobType.GOOGLE_NEWS)
        self.assertEqual(job.metadata["query_policy"], "when:7d")
        self.assertEqual(
            job.metadata["candidate_distribution"],
            "round_robin_query_batches",
        )
        self.assertEqual(job.metadata["funnel"]["feed_entries_found"], 1)
        self.assertEqual(job.metadata["funnel"]["candidates_checked"], 1)
        self.assertEqual(
            job.metadata["funnel"]["publisher_urls_resolved"],
            1,
        )
        self.assertEqual(job.metadata["funnel"]["sources_matched"], 1)
        self.assertEqual(
            job.metadata["funnel"]["article_payloads_eligible"],
            1,
        )
        article = Article.objects.get()
        self.assertEqual(article.source, self.source)
        self.assertEqual(
            article.raw_metadata["google_news"]["discovery_scope"],
            "global_allowed_sources",
        )
        self.assertEqual(
            article.raw_metadata["google_news"]["mapped_source_code"],
            self.source.code,
        )
        self.client.force_login(self.user)
        response = self.client.get(
            reverse(
                "dashboard:crawler-job-detail",
                kwargs={"job_id": job.id},
            )
        )
        self.assertContains(response, "Alur Penyaringan Google News")
        self.assertContains(response, "URL penerbit terurai")

    @patch("apps.entities.services.exploit_article")
    @patch("apps.crawlers.google_news_crawler.CrawlerHttpClient")
    @patch.object(GoogleNewsRssCrawler, "_parse_article")
    def test_result_from_second_registered_source_is_accepted_as_that_source(
        self,
        mock_parse_article,
        mock_client,
        _mock_exploit_article,
    ):
        other = self._create_source(
            name="Media Lain",
            code="media-lain",
            domain="other.example",
        )
        google_url = "https://news.google.com/rss/articles/token"
        publisher_url = "https://other.example/read/kasus-dbd"
        self._mock_client(
            mock_client,
            feed_text=self._feed(google_url, source_title="BBC"),
            final_url=publisher_url,
        )
        mock_parse_article.return_value = self._payload(
            publisher_url,
            other.code,
        )
        result = run_crawler(GoogleNewsRssCrawler(), trigger_type="test")
        self.assertEqual(result.total_created, 1)
        self.assertEqual(Article.objects.get().source, other)

    @patch("apps.crawlers.google_news_crawler.CrawlerHttpClient")
    @patch.object(GoogleNewsRssCrawler, "_parse_article")
    def test_unresolved_google_link_is_metadata_only(
        self,
        mock_parse_article,
        mock_client,
    ):
        google_url = "https://news.google.com/rss/articles/token"
        client = self._mock_client(
            mock_client,
            feed_text=self._feed(google_url),
            final_url=google_url,
        )
        result = run_crawler(GoogleNewsRssCrawler(), trigger_type="test")
        self.assertEqual(result.total_rejected, 1)
        item = CollectionJobItem.objects.get()
        self.assertEqual(item.status, CollectionJobItem.Status.METADATA_ONLY)
        mock_parse_article.assert_not_called()

    @patch("apps.entities.services.exploit_article")
    @patch("apps.crawlers.google_news_crawler.CrawlerHttpClient")
    @patch.object(GoogleNewsRssCrawler, "_parse_article")
    def test_interstitial_explicit_publisher_link_is_mapped(
        self,
        mock_parse_article,
        mock_client,
        _mock_exploit_article,
    ):
        google_url = "https://news.google.com/rss/articles/token"
        publisher_url = "https://example.com/read/kasus-dbd-interstitial"
        client = self._mock_client(
            mock_client,
            feed_text=self._feed(google_url),
            final_url=google_url,
            interstitial_html=(
                f"<html><body><a href='{publisher_url}'>Baca</a></body></html>"
            ),
        )
        client.get_html.return_value = HttpPage(
            requested_url=publisher_url,
            final_url=publisher_url,
            status_code=200,
            content_type="text/html; charset=utf-8",
            text="<html><body>publisher page</body></html>",
        )
        mock_parse_article.return_value = self._payload(
            publisher_url,
            self.source.code,
        )
        result = run_crawler(GoogleNewsRssCrawler(), trigger_type="test")
        self.assertEqual(result.total_created, 1)

    @patch("apps.entities.services.exploit_article")
    @patch("apps.crawlers.google_news_crawler.CrawlerHttpClient")
    @patch.object(GoogleNewsRssCrawler, "_parse_article")
    def test_opaque_token_is_resolved_through_google_news_rpc(
        self,
        mock_parse_article,
        mock_client,
        _mock_exploit_article,
    ):
        token = "CBMi2AFBVV95cUxPMVRqS12345678"
        google_url = f"https://news.google.com/rss/articles/{token}?oc=5"
        publisher_url = "https://example.com/read/rabies-padang"
        client = self._mock_client(
            mock_client,
            feed_text=self._feed(google_url),
            final_url=google_url,
            interstitial_html=(
                '<c-wiz><div jscontroller="x" '
                'data-n-a-sg="ATR1dL_signature" '
                'data-n-a-ts="1725891265"></div></c-wiz>'
            ),
        )
        inner_response = json.dumps(
            ["garturlres", publisher_url, 1],
            separators=(",", ":"),
        )
        rpc_text = (
            ")]}'\n\n"
            + json.dumps(
                [
                    [
                        "wrb.fr",
                        "Fbv4je",
                        inner_response,
                        None,
                        None,
                        None,
                        "generic",
                    ]
                ]
            )
        )
        client.post_discovery_form.return_value = HttpPage(
            requested_url="https://news.google.com/_/DotsSplashUi/data/batchexecute",
            final_url="https://news.google.com/_/DotsSplashUi/data/batchexecute",
            status_code=200,
            content_type="application/json",
            text=rpc_text,
        )
        client.get_html.return_value = HttpPage(
            requested_url=publisher_url,
            final_url=publisher_url,
            status_code=200,
            content_type="text/html; charset=utf-8",
            text="<html><body>publisher page</body></html>",
        )
        mock_parse_article.return_value = self._payload(
            publisher_url,
            self.source.code,
        )

        result = run_crawler(GoogleNewsRssCrawler(), trigger_type="test")

        self.assertEqual(result.total_created, 1)
        article = Article.objects.get()
        self.assertEqual(article.original_url, publisher_url)
        self.assertEqual(
            article.raw_metadata["google_news"]["resolver_method"],
            "google_news_article_rpc",
        )
        form = client.post_discovery_form.call_args.kwargs["data"]["f.req"]
        self.assertIn(token, form)
        self.assertEqual(
            extract_publisher_url_from_rpc_response(rpc_text),
            publisher_url,
        )

    @patch("apps.crawlers.google_news_crawler.CrawlerHttpClient")
    @patch.object(GoogleNewsRssCrawler, "_parse_article")
    def test_publisher_waf_is_fetch_blocked(
        self,
        mock_parse_article,
        mock_client,
    ):
        google_url = "https://news.google.com/rss/articles/token"
        self._mock_client(
            mock_client,
            feed_text=self._feed(google_url),
            final_url="https://example.com/read/kasus-dbd",
            publisher_error=CrawlerHttpError("HTTP 403"),
        )
        result = run_crawler(GoogleNewsRssCrawler(), trigger_type="test")
        self.assertEqual(result.total_failed, 1)
        item = CollectionJobItem.objects.get()
        self.assertEqual(item.status, CollectionJobItem.Status.FETCH_BLOCKED)
        self.assertEqual(item.metadata["stage"], "publisher_fetch_blocked")
        mock_parse_article.assert_not_called()

    @patch("apps.crawlers.google_news_crawler.CrawlerHttpClient")
    @patch.object(GoogleNewsRssCrawler, "_parse_article")
    def test_unregistered_publisher_domain_is_rejected(
        self,
        mock_parse_article,
        mock_client,
    ):
        google_url = "https://news.google.com/rss/articles/token"
        self._mock_client(
            mock_client,
            feed_text=self._feed(google_url),
            final_url="https://unlisted.example.org/read/kasus-dbd",
        )
        result = run_crawler(GoogleNewsRssCrawler(), trigger_type="test")
        self.assertEqual(result.total_rejected, 1)
        self.assertEqual(
            CollectionJobItem.objects.get().metadata["stage"],
            "publisher_source_mapping",
        )
        mock_parse_article.assert_not_called()

    @patch("apps.crawlers.google_news_crawler.CrawlerHttpClient")
    @patch.object(GoogleNewsRssCrawler, "_parse_article")
    def test_registered_domain_with_disallowed_path_is_rejected(
        self,
        mock_parse_article,
        mock_client,
    ):
        google_url = "https://news.google.com/rss/articles/token"
        self._mock_client(
            mock_client,
            feed_text=self._feed(google_url),
            final_url="https://example.com/video/kasus-dbd",
        )
        result = run_crawler(GoogleNewsRssCrawler(), trigger_type="test")
        self.assertEqual(result.total_rejected, 1)
        self.assertEqual(
            CollectionJobItem.objects.get().metadata["stage"],
            "publisher_url_validation",
        )
        mock_parse_article.assert_not_called()

    @patch("apps.crawlers.google_news_crawler.CrawlerHttpClient")
    @patch.object(GoogleNewsRssCrawler, "_parse_article")
    def test_stale_entry_is_rejected_before_resolution(
        self,
        mock_parse_article,
        mock_client,
    ):
        google_url = "https://news.google.com/rss/articles/token"
        old_date = (timezone.now() - timedelta(days=8)).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        client = self._mock_client(
            mock_client,
            feed_text=self._feed(google_url, published=old_date),
            final_url="https://example.com/read/kasus-dbd",
        )
        result = run_crawler(
            GoogleNewsRssCrawler(max_age_days=7),
            trigger_type="test",
        )
        self.assertEqual(result.total_rejected, 1)
        self.assertEqual(
            CollectionJobItem.objects.get().metadata["stage"],
            "google_news_entry_age",
        )
        client.resolve_discovery_url.assert_not_called()
        mock_parse_article.assert_not_called()

    @patch("apps.crawlers.google_news_crawler.CrawlerHttpClient")
    def test_candidate_limit_caps_stale_audit_rows(self, mock_client):
        old_date = (timezone.now() - timedelta(days=8)).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        links = tuple(
            f"https://news.google.com/rss/articles/token-{index}"
            for index in range(10)
        )
        self._mock_client(
            mock_client,
            feed_text=self._feed(*links, published=old_date),
            final_url="https://example.com/read/unused",
        )
        result = run_crawler(
            GoogleNewsRssCrawler(candidate_limit=3),
            trigger_type="test",
        )
        self.assertEqual(result.total_rejected, 3)
        self.assertEqual(CollectionJobItem.objects.count(), 3)

    @patch("apps.crawlers.google_news_crawler.build_google_news_disease_scope")
    @patch("apps.crawlers.google_news_crawler.CrawlerHttpClient")
    def test_candidate_limit_is_distributed_round_robin_across_batches(
        self,
        mock_client,
        mock_scope,
    ):
        batches = (
            SimpleNamespace(
                index=1,
                total=2,
                query="DBD",
                terms=("DBD",),
            ),
            SimpleNamespace(
                index=2,
                total=2,
                query="malaria",
                terms=("malaria",),
            ),
        )
        mock_scope.return_value = SimpleNamespace(batches=batches)
        old_date = (timezone.now() - timedelta(days=8)).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        first_url = GoogleNewsRssCrawler.build_feed_url(
            query_text="DBD",
            max_age_days=7,
        )
        second_url = GoogleNewsRssCrawler.build_feed_url(
            query_text="malaria",
            max_age_days=7,
        )
        client = mock_client.return_value.__enter__.return_value
        client.get_discovery_feed.side_effect = (
            HttpPage(
                requested_url=first_url,
                final_url=first_url,
                status_code=200,
                content_type="application/rss+xml",
                text=self._feed(
                    *(
                        "https://news.google.com/rss/articles/dbd-"
                        f"{index}"
                        for index in range(5)
                    ),
                    published=old_date,
                ),
            ),
            HttpPage(
                requested_url=second_url,
                final_url=second_url,
                status_code=200,
                content_type="application/rss+xml",
                text=self._feed(
                    "https://news.google.com/rss/articles/malaria-1",
                    published=old_date,
                ),
            ),
        )

        result = run_crawler(
            GoogleNewsRssCrawler(candidate_limit=3),
            trigger_type="test",
        )

        self.assertEqual(result.total_rejected, 3)
        self.assertEqual(client.get_discovery_feed.call_count, 2)
        self.assertEqual(
            list(
                CollectionJobItem.objects.order_by("created_at").values_list(
                    "metadata__query_batch",
                    flat=True,
                )
            ),
            [1, 2, 1],
        )
        job = CollectionJob.objects.get(id=result.job_id)
        self.assertEqual(job.metadata["funnel"]["feed_entries_found"], 6)
        self.assertEqual(job.metadata["funnel"]["candidates_checked"], 3)
        client.resolve_discovery_url.assert_not_called()

    def test_management_command_lists_allowed_sources(self):
        output = StringIO()
        call_command(
            "run_google_news_crawler",
            "--list-allowed",
            stdout=output,
        )
        self.assertIn(self.source.code, output.getvalue())

    def test_collection_page_exposes_one_global_channel(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard:crawler-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jalankan Google News")
        self.assertContains(response, "Global · 1 sumber diizinkan")
        self.assertNotContains(response, 'id="google-news-source"')

    @patch("apps.dashboard.views.enqueue_crawler")
    def test_collection_endpoint_starts_one_global_crawler(
        self,
        mock_run_in_background,
    ):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("dashboard:crawler-run"),
            {
                "channel": "google_news",
                "source": "global",
                "limit": "5",
                "candidate_limit": "10",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["started"], ["google-news-global"])
        crawler = mock_run_in_background.call_args.args[0]
        self.assertIsInstance(crawler, GoogleNewsRssCrawler)
        self.assertTrue(crawler.global_discovery)

    def test_settings_page_explains_global_whitelist_and_age(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("dashboard:google-news-discovery-settings")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Penemuan Global Google News")
        self.assertContains(
            response,
            "Daftar Sumber Penerbit yang Diizinkan",
        )
        self.assertContains(response, "7 hari")
        self.assertContains(response, "when:7d")
        self.assertContains(response, "bergiliran antarbatch")
        self.assertContains(response, self.source.domain)

    def test_source_detail_shows_automatic_allowlist_status(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse(
                "dashboard:source-detail",
                kwargs={"source_id": self.source.id},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Diizinkan")
        self.assertContains(response, "Otomatis dari Disease Master")
        self.assertNotContains(response, "Tambah Kueri Discovery")
