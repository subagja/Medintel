from django.test import TestCase

from .models import Source, SourceUrlPattern
from .services import (
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
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

        SourceUrlPattern.objects.create(
            source=self.source,
            pattern="/read/",
            pattern_type=SourceUrlPattern.PatternType.ALLOW,
        )

        SourceUrlPattern.objects.create(
            source=self.source,
            pattern="/advertorial/",
            pattern_type=SourceUrlPattern.PatternType.DENY,
        )

    def test_accepts_valid_article_url(self):
        result = validate_source_url(
            self.source,
            "https://www.example.com/read/berita-dbd"
            "?utm_source=facebook",
        )

        self.assertTrue(result.is_valid)
        self.assertEqual(
            result.normalized_url,
            "https://example.com/read/berita-dbd",
        )

    def test_accepts_subdomain(self):
        result = validate_source_url(
            self.source,
            "https://health.example.com/read/berita-dbd",
        )

        self.assertTrue(result.is_valid)

    def test_rejects_different_domain(self):
        result = validate_source_url(
            self.source,
            "https://example-news.com/read/berita-dbd",
        )

        self.assertFalse(result.is_valid)
        self.assertIn(
            "Domain URL tidak sesuai",
            result.reason,
        )

    def test_rejects_denied_pattern(self):
        result = validate_source_url(
            self.source,
            "https://example.com/read/advertorial/produk",
        )

        self.assertFalse(result.is_valid)
        self.assertIn(
            "pola penolakan",
            result.reason,
        )

    def test_rejects_url_without_allow_pattern(self):
        result = validate_source_url(
            self.source,
            "https://example.com/video/berita-dbd",
        )

        self.assertFalse(result.is_valid)
        self.assertIn(
            "tidak cocok dengan pola",
            result.reason,
        )

    def test_rejects_unverified_source(self):
        self.source.is_verified = False
        self.source.save(
            update_fields=["is_verified"],
        )

        result = validate_source_url(
            self.source,
            "https://example.com/read/berita-dbd",
        )

        self.assertFalse(result.is_valid)
        self.assertEqual(
            result.reason,
            "Sumber belum tervalidasi.",
        )