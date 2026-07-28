from django.test import TestCase

from apps.articles.models import Article
from apps.entities.models import (
    ArticleDisease,
    ArticleLocation,
    Disease,
    DiseaseAlias,
    ExtractionMethod,
    Location,
    LocationAlias,
    ValidationStatus,
)
from apps.entities.services import extract_article_entities
from apps.sources.models import Source


class RuleBasedEntityExtractionTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media Ekstraksi",
            code="media-ekstraksi",
            domain="extract.example.com",
            base_url="https://extract.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

        self.disease = Disease.objects.create(
            name="Demam Berdarah Dengue",
            canonical_name="Dengue",
            code="dbd-extraction",
            is_active=True,
        )

        DiseaseAlias.objects.create(
            disease=self.disease,
            alias="DBD",
        )

        DiseaseAlias.objects.create(
            disease=self.disease,
            alias="demam berdarah",
        )

        indonesia = Location.objects.create(
            name="Indonesia",
            administrative_level=(
                Location.AdministrativeLevel.COUNTRY
            ),
            country_code="ID",
        )

        jawa_barat = Location.objects.create(
            name="Jawa Barat",
            administrative_level=(
                Location.AdministrativeLevel.PROVINCE
            ),
            parent=indonesia,
            country_code="ID",
        )

        self.bandung = Location.objects.create(
            name="Kabupaten Bandung",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            parent=jawa_barat,
            country_code="ID",
        )

        LocationAlias.objects.create(
            location=self.bandung,
            alias="Kab. Bandung",
        )

        self.article = Article.objects.create(
            source=self.source,
            original_url=(
                "https://extract.example.com/read/dbd-bandung"
            ),
            normalized_url=(
                "https://extract.example.com/read/dbd-bandung"
            ),
            title="Kasus DBD meningkat di Kabupaten Bandung",
            content_text=(
                "Dinas kesehatan melaporkan peningkatan kasus "
                "demam berdarah di Kabupaten Bandung. "
                "Penanganan DBD sedang dilakukan."
            ),
            content_hash="b" * 64,
            processing_status=Article.ProcessingStatus.VALIDATED,
        )

    def test_extracts_disease_and_location(self):
        result = extract_article_entities(
            self.article
        )

        self.assertEqual(
            result.diseases_created,
            1,
        )
        self.assertEqual(
            result.locations_created,
            1,
        )

        disease_relation = ArticleDisease.objects.get(
            article=self.article,
            disease=self.disease,
        )

        self.assertEqual(
            disease_relation.extraction_method,
            ExtractionMethod.RULE_BASED,
        )
        self.assertEqual(
            disease_relation.validation_status,
            ValidationStatus.UNREVIEWED,
        )

        location_relation = ArticleLocation.objects.get(
            article=self.article,
            location=self.bandung,
        )

        self.assertEqual(
            location_relation.extraction_method,
            ExtractionMethod.RULE_BASED,
        )

    def test_extraction_is_idempotent(self):
        extract_article_entities(
            self.article
        )
        second_result = extract_article_entities(
            self.article
        )

        self.assertEqual(
            ArticleDisease.objects.count(),
            1,
        )
        self.assertEqual(
            ArticleLocation.objects.count(),
            1,
        )

        self.assertEqual(
            second_result.diseases_created,
            0,
        )
        self.assertEqual(
            second_result.diseases_updated,
            1,
        )

    def test_disease_confidence_increases_for_title_match(self):
        extract_article_entities(
            self.article
        )

        relation = ArticleDisease.objects.get(
            article=self.article,
            disease=self.disease,
        )

        self.assertGreaterEqual(
            relation.confidence_score,
            0.85,
        )