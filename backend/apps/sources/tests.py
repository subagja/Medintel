from django.test import TestCase

from .models import (
    Source,
    SourceSeedUrl,
    SourceUrlPattern,
)
from .forms import (
    SourceSeedUrlForm,
    SourceUrlPatternForm,
)
from .services import (
    check_source_crawl_readiness,
    normalize_url,
    validate_source_url,
)


class NormalizeUrlTests(TestCase):
    def test_removes_www_and_tracking_parameters(self):
        result = normalize_url(
            "https://www.example.com/read/news/"
            "?utm_source=facebook&id=10"
        )

        self.assertEqual(
            result,
            "https://example.com/read/news?id=10",
        )

    def test_removes_fragment(self):
        result = normalize_url(
            "https://example.com/read/news#section"
        )

        self.assertEqual(
            result,
            "https://example.com/read/news",
        )

    def test_rejects_invalid_scheme(self):
        with self.assertRaises(ValueError):
            normalize_url(
                "ftp://example.com/read/news"
            )


class SourceUrlValidationTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media Uji",
            code="media-uji",
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

        self.allow_pattern = (
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
        )

        self.deny_pattern = (
            SourceUrlPattern.objects.create(
                source=self.source,
                pattern="/advertorial/",
                pattern_type=(
                    SourceUrlPattern.PatternType.DENY
                ),
                match_type=(
                    SourceUrlPattern.MatchType.CONTAINS
                ),
                priority=10,
                is_active=True,
            )
        )

    def test_accepts_valid_article_url(self):
        result = validate_source_url(
            self.source,
            (
                "https://www.example.com/read/berita-dbd"
                "?utm_source=facebook"
            ),
        )

        self.assertTrue(
            result.is_valid
        )

        self.assertEqual(
            result.normalized_url,
            "https://example.com/read/berita-dbd",
        )

    def test_accepts_subdomain_when_allowed(self):
        self.source.allow_subdomains = True
        self.source.save()

        result = validate_source_url(
            self.source,
            "https://health.example.com/read/berita-dbd",
        )

        self.assertTrue(
            result.is_valid
        )

    def test_rejects_subdomain_when_not_allowed(self):
        result = validate_source_url(
            self.source,
            "https://health.example.com/read/berita-dbd",
        )

        self.assertFalse(
            result.is_valid
        )

        self.assertIn(
            "Domain URL tidak sesuai",
            result.reason,
        )

    def test_rejects_different_domain(self):
        result = validate_source_url(
            self.source,
            "https://example-news.com/read/berita-dbd",
        )

        self.assertFalse(
            result.is_valid
        )

        self.assertIn(
            "Domain URL tidak sesuai",
            result.reason,
        )

    def test_rejects_denied_pattern(self):
        result = validate_source_url(
            self.source,
            (
                "https://example.com/"
                "read/advertorial/produk"
            ),
        )

        self.assertFalse(
            result.is_valid
        )

        self.assertIn(
            "pola penolakan",
            result.reason,
        )

        self.assertEqual(
            result.matched_pattern_id,
            str(self.deny_pattern.id),
        )

    def test_rejects_url_without_allow_pattern(self):
        result = validate_source_url(
            self.source,
            "https://example.com/video/berita-dbd",
        )

        self.assertFalse(
            result.is_valid
        )

        self.assertIn(
            "tidak cocok dengan pola",
            result.reason,
        )

    def test_rejects_unverified_source(self):
        self.source.is_verified = False
        self.source.crawl_enabled = False
        self.source.save()

        result = validate_source_url(
            self.source,
            "https://example.com/read/berita-dbd",
        )

        self.assertFalse(
            result.is_valid
        )

        self.assertEqual(
            result.reason,
            "Sumber belum tervalidasi.",
        )

    def test_rejects_inactive_source(self):
        self.source.is_active = False
        self.source.crawl_enabled = False
        self.source.save()

        result = validate_source_url(
            self.source,
            "https://example.com/read/berita-dbd",
        )

        self.assertFalse(
            result.is_valid
        )

        self.assertEqual(
            result.reason,
            "Sumber tidak aktif.",
        )

    def test_deny_pattern_overrides_allow_pattern(self):
        result = validate_source_url(
            self.source,
            (
                "https://example.com/"
                "read/advertorial/produk-dbd"
            ),
        )

        self.assertFalse(
            result.is_valid
        )

        self.assertIn(
            "pola penolakan",
            result.reason,
        )


class SourceCrawlReadinessTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media Kesiapan",
            code="media-kesiapan",
            domain="example.com",
            base_url="https://example.com",
            source_type=(
                Source.SourceType.NATIONAL_MEDIA
            ),
            is_verified=True,
            is_active=True,
            crawl_enabled=True,
        )

    def test_requires_active_seed_and_allow_pattern(self):
        readiness = check_source_crawl_readiness(
            self.source
        )

        self.assertFalse(readiness.is_ready)
        self.assertIn(
            "Sumber belum memiliki URL awal aktif.",
            readiness.errors,
        )
        self.assertIn(
            "Sumber belum memiliki aturan URL allow.",
            readiness.errors,
        )

    def test_source_is_ready_when_all_requirements_pass(self):
        SourceSeedUrl.objects.create(
            source=self.source,
            url="https://example.com/berita",
            seed_type=SourceSeedUrl.SeedType.LISTING,
            is_active=True,
        )
        SourceUrlPattern.objects.create(
            source=self.source,
            pattern="/berita/",
            pattern_type=(
                SourceUrlPattern.PatternType.ALLOW
            ),
            match_type=(
                SourceUrlPattern.MatchType.PREFIX
            ),
            is_active=True,
        )

        readiness = check_source_crawl_readiness(
            self.source
        )

        self.assertTrue(readiness.is_ready)
        self.assertEqual(readiness.errors, ())

    def test_inactive_configuration_does_not_count(self):
        SourceSeedUrl.objects.create(
            source=self.source,
            url="https://example.com/berita",
            seed_type=SourceSeedUrl.SeedType.LISTING,
            is_active=False,
        )
        SourceUrlPattern.objects.create(
            source=self.source,
            pattern="/berita/",
            pattern_type=(
                SourceUrlPattern.PatternType.ALLOW
            ),
            match_type=(
                SourceUrlPattern.MatchType.PREFIX
            ),
            is_active=False,
        )

        readiness = check_source_crawl_readiness(
            self.source
        )

        self.assertFalse(readiness.is_ready)

    def test_manual_strategy_is_not_automatic_crawl_ready(self):
        self.source.crawl_strategy = (
            Source.CrawlStrategy.MANUAL
        )
        self.source.save()

        readiness = check_source_crawl_readiness(
            self.source
        )

        self.assertFalse(readiness.is_ready)
        self.assertIn(
            (
                "Strategi manual tidak dapat diproses "
                "oleh crawler otomatis."
            ),
            readiness.errors,
        )


class SourceConfigurationFormTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media Form",
            code="media-form",
            domain="example.com",
            base_url="https://example.com",
            source_type=(
                Source.SourceType.NATIONAL_MEDIA
            ),
            is_verified=True,
            is_active=True,
            crawl_enabled=True,
        )

    def test_seed_form_rejects_different_domain(self):
        form = SourceSeedUrlForm(
            data={
                "url": "https://other.example/berita",
                "seed_type": (
                    SourceSeedUrl.SeedType.LISTING
                ),
                "priority": 100,
                "is_active": True,
                "notes": "",
            },
            source=self.source,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("url", form.errors)

    def test_seed_form_rejects_duplicate_url(self):
        SourceSeedUrl.objects.create(
            source=self.source,
            url="https://example.com/berita",
            seed_type=SourceSeedUrl.SeedType.LISTING,
        )
        form = SourceSeedUrlForm(
            data={
                "url": "https://example.com/berita",
                "seed_type": (
                    SourceSeedUrl.SeedType.LISTING
                ),
                "priority": 100,
                "is_active": True,
                "notes": "",
            },
            source=self.source,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("url", form.errors)

    def test_pattern_form_rejects_duplicate_rule(self):
        SourceUrlPattern.objects.create(
            source=self.source,
            pattern="/berita/",
            pattern_type=(
                SourceUrlPattern.PatternType.ALLOW
            ),
            match_type=(
                SourceUrlPattern.MatchType.PREFIX
            ),
        )
        form = SourceUrlPatternForm(
            data={
                "pattern_type": (
                    SourceUrlPattern.PatternType.ALLOW
                ),
                "match_type": (
                    SourceUrlPattern.MatchType.PREFIX
                ),
                "pattern": "/berita/",
                "priority": 100,
                "description": "",
                "is_active": True,
            },
            source=self.source,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("pattern", form.errors)
