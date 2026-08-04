from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import TestCase

from apps.articles.models import Article
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ExtractionMethod,
    Location,
    ValidationStatus,
)
from apps.entities.services import (
    exploit_article,
    extract_article_facts,
)
from apps.sources.models import Source


class RuleBasedFactExtractionTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media Fakta",
            code="media-fakta",
            domain="facts.example.com",
            base_url="https://facts.example.com",
            source_type=(
                Source.SourceType.NATIONAL_MEDIA
            ),
            is_verified=True,
            is_active=True,
        )

        self.disease, _ = Disease.objects.get_or_create(
            name="Demam Berdarah Dengue",
            defaults={
                "canonical_name": "Dengue",
                "code": "dbd-fact",
                "is_active": True,
            },
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

        self.article = Article.objects.create(
            source=self.source,
            original_url=(
                "https://facts.example.com/read/dbd"
            ),
            normalized_url=(
                "https://facts.example.com/read/dbd"
            ),
            title=(
                "Kasus DBD meningkat di Kabupaten Bandung"
            ),
            content_text=(
                "Pada 28 Juli 2026, jumlah kasus DBD "
                "meningkat menjadi 42 kasus. "
                "Sebanyak 2 pasien meninggal dan "
                "5 pasien masih dirawat."
            ),
            published_at=datetime(
                2026,
                7,
                28,
                8,
                0,
                tzinfo=ZoneInfo("Asia/Jakarta"),
            ),
            content_hash="c" * 64,
            processing_status=(
                Article.ProcessingStatus.VALIDATED
            ),
        )

        ArticleDisease.objects.create(
            article=self.article,
            disease=self.disease,
            mention_text="DBD",
            confidence_score=0.95,
            extraction_method=(
                ExtractionMethod.RULE_BASED
            ),
            is_primary=True,
            validation_status=(
                ValidationStatus.UNREVIEWED
            ),
        )

        ArticleLocation.objects.create(
            article=self.article,
            location=self.bandung,
            mention_text="Kabupaten Bandung",
            confidence_score=0.95,
            extraction_method=(
                ExtractionMethod.RULE_BASED
            ),
            is_primary=True,
            validation_status=(
                ValidationStatus.UNREVIEWED
            ),
        )

    def test_extracts_structured_fact(self):
        result = extract_article_facts(
            self.article
        )

        self.assertEqual(
            result.facts_created,
            1,
        )

        fact = ArticleFact.objects.get(
            article=self.article
        )

        self.assertEqual(
            fact.disease,
            self.disease,
        )

        self.assertEqual(
            fact.location,
            self.bandung,
        )

        self.assertEqual(
            fact.case_count,
            42,
        )

        self.assertEqual(
            fact.death_count,
            2,
        )

        self.assertEqual(
            fact.hospitalized_count,
            5,
        )

        self.assertEqual(
            fact.event_date.isoformat(),
            "2026-07-28",
        )

        self.assertEqual(
            fact.trend,
            ArticleFact.Trend.INCREASING,
        )

        self.assertEqual(
            fact.validation_status,
            ValidationStatus.UNREVIEWED,
        )

    def test_fact_extraction_is_idempotent(self):
        extract_article_facts(
            self.article
        )

        second_result = extract_article_facts(
            self.article
        )

        self.assertEqual(
            ArticleFact.objects.count(),
            1,
        )

        self.assertEqual(
            second_result.facts_created,
            0,
        )

        self.assertEqual(
            second_result.facts_skipped,
            1,
        )

    def test_pipeline_marks_article_processed(self):
        exploit_article(
            self.article
        )

        self.article.refresh_from_db()

        self.assertEqual(
            self.article.processing_status,
            Article.ProcessingStatus.PROCESSED,
        )
