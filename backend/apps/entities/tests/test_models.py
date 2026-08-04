from datetime import date

from django.test import TestCase

from apps.articles.models import Article
from apps.sources.models import Source

from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    DiseaseAlias,
    ExtractionMethod,
    Location,
    ValidationStatus,
)


class EntityModelTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media Uji",
            code="media-uji-entities",
            domain="entities.example.com",
            base_url="https://entities.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

        self.article = Article.objects.create(
            source=self.source,
            original_url=(
                "https://entities.example.com/read/dbd-bandung"
            ),
            normalized_url=(
                "https://entities.example.com/read/dbd-bandung"
            ),
            title="Kasus DBD meningkat di Kabupaten Bandung",
            content_text=(
                "Dinas kesehatan melaporkan peningkatan kasus "
                "demam berdarah di Kabupaten Bandung."
            ),
            content_hash="a" * 64,
            processing_status=Article.ProcessingStatus.VALIDATED,
        )

        self.disease, _ = Disease.objects.get_or_create(
            name="Demam Berdarah Dengue",
            defaults={
                "canonical_name": "Dengue Hemorrhagic Fever",
                "code": "demam-berdarah-dengue",
                "category": "Vector-borne",
                "is_priority": True,
            },
        )

        DiseaseAlias.objects.get_or_create(
            disease=self.disease,
            alias="DBD",
            defaults={"language": "id"},
        )

        self.indonesia = Location.objects.create(
            name="Indonesia",
            code="ID",
            administrative_level=Location.AdministrativeLevel.COUNTRY,
            country_code="ID",
        )

        self.jawa_barat = Location.objects.create(
            name="Jawa Barat",
            code="32",
            administrative_level=Location.AdministrativeLevel.PROVINCE,
            parent=self.indonesia,
            country_code="ID",
        )

        self.bandung = Location.objects.create(
            name="Kabupaten Bandung",
            code="3204",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            parent=self.jawa_barat,
            country_code="ID",
        )

    def test_article_can_be_linked_to_disease(self):
        relation = ArticleDisease.objects.create(
            article=self.article,
            disease=self.disease,
            mention_text="demam berdarah",
            confidence_score=0.95,
            extraction_method=ExtractionMethod.MANUAL,
            is_primary=True,
            validation_status=ValidationStatus.VALIDATED,
        )

        self.assertEqual(
            relation.disease,
            self.disease,
        )

        self.assertIn(
            self.disease,
            self.article.diseases.all(),
        )

    def test_article_can_be_linked_to_location(self):
        relation = ArticleLocation.objects.create(
            article=self.article,
            location=self.bandung,
            mention_text="Kabupaten Bandung",
            confidence_score=0.98,
            extraction_method=ExtractionMethod.MANUAL,
            is_primary=True,
            validation_status=ValidationStatus.VALIDATED,
        )

        self.assertEqual(
            relation.location,
            self.bandung,
        )

        self.assertIn(
            self.bandung,
            self.article.locations.all(),
        )

    def test_article_fact_can_store_case_information(self):
        fact = ArticleFact.objects.create(
            article=self.article,
            disease=self.disease,
            location=self.bandung,
            event_date=date(2026, 7, 28),
            case_count=42,
            death_count=2,
            trend=ArticleFact.Trend.INCREASING,
            fact_text=(
                "Kasus DBD meningkat menjadi 42 kasus "
                "dengan dua kematian."
            ),
            confidence_score=0.90,
            extraction_method=ExtractionMethod.MANUAL,
            validation_status=ValidationStatus.VALIDATED,
        )

        self.assertEqual(fact.case_count, 42)
        self.assertEqual(fact.death_count, 2)
        self.assertEqual(
            fact.trend,
            ArticleFact.Trend.INCREASING,
        )

    def test_location_hierarchy(self):
        self.assertEqual(
            self.bandung.parent,
            self.jawa_barat,
        )

        self.assertEqual(
            self.jawa_barat.parent,
            self.indonesia,
        )
