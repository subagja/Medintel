from django.test import TestCase

from apps.articles.models import Article
from apps.sources.models import Source, SourceUrlPattern

from .dto import ArticlePayload
from .services import ingest_article


class ArticleIngestionTests(TestCase):
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

        self.valid_content = (
            "Terjadi peningkatan kasus demam berdarah di wilayah uji. "
            "Dinas kesehatan melakukan penyelidikan epidemiologi dan "
            "mengimbau masyarakat untuk memberantas sarang nyamuk."
        )

    def test_ingests_valid_article(self):
        payload = ArticlePayload(
            source_code="media-uji",
            url="https://example.com/read/kasus-dbd",
            title="Peningkatan kasus DBD di wilayah uji",
            content=self.valid_content,
        )

        result = ingest_article(payload)

        self.assertTrue(result.created)
        self.assertEqual(result.status, "created")
        self.assertEqual(Article.objects.count(), 1)

        article = Article.objects.get()

        self.assertEqual(
            article.processing_status,
            Article.ProcessingStatus.VALIDATED,
        )

    def test_rejects_unknown_source(self):
        payload = ArticlePayload(
            source_code="tidak-terdaftar",
            url="https://example.com/read/kasus-dbd",
            title="Peningkatan kasus DBD di wilayah uji",
            content=self.valid_content,
        )

        result = ingest_article(payload)

        self.assertFalse(result.created)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(
            result.reason,
            "Sumber tidak terdaftar.",
        )

    def test_rejects_invalid_domain(self):
        payload = ArticlePayload(
            source_code="media-uji",
            url="https://domain-lain.com/read/kasus-dbd",
            title="Peningkatan kasus DBD di wilayah uji",
            content=self.valid_content,
        )

        result = ingest_article(payload)

        self.assertFalse(result.created)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(Article.objects.count(), 0)

    def test_detects_duplicate_url(self):
        payload = ArticlePayload(
            source_code="media-uji",
            url="https://example.com/read/kasus-dbd",
            title="Peningkatan kasus DBD di wilayah uji",
            content=self.valid_content,
        )

        first_result = ingest_article(payload)
        second_result = ingest_article(payload)

        self.assertTrue(first_result.created)
        self.assertFalse(second_result.created)
        self.assertEqual(second_result.status, "duplicate")
        self.assertEqual(Article.objects.count(), 1)

    def test_detects_duplicate_content(self):
        first_payload = ArticlePayload(
            source_code="media-uji",
            url="https://example.com/read/kasus-dbd",
            title="Peningkatan kasus DBD di wilayah uji",
            content=self.valid_content,
        )

        second_payload = ArticlePayload(
            source_code="media-uji",
            url="https://example.com/read/kasus-dbd-versi-2",
            title="Peningkatan kasus DBD di wilayah uji",
            content=self.valid_content,
        )

        first_result = ingest_article(first_payload)
        second_result = ingest_article(second_payload)

        self.assertTrue(first_result.created)
        self.assertFalse(second_result.created)
        self.assertEqual(second_result.status, "duplicate")
        self.assertEqual(Article.objects.count(), 1)

    def test_rejects_short_content(self):
        payload = ArticlePayload(
            source_code="media-uji",
            url="https://example.com/read/kasus-dbd",
            title="Peningkatan kasus DBD",
            content="Isi terlalu pendek.",
        )

        result = ingest_article(payload)

        self.assertFalse(result.created)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(Article.objects.count(), 0)