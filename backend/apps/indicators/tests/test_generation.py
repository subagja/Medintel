from datetime import date

from django.test import TestCase

from apps.articles.models import Article
from apps.entities.models import (
    ArticleFact,
    Disease,
    ExtractionMethod,
    Location,
    ValidationStatus,
)
from apps.indicators.models import (
    Indicator,
    IndicatorEvidence,
    IndicatorType,
)
from apps.indicators.services import (
    generate_indicators_from_fact,
)
from apps.sources.models import Source


class IndicatorGenerationTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media Indikator",
            code="media-indikator",
            domain="indicator.example.com",
            base_url="https://indicator.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

        self.article = Article.objects.create(
            source=self.source,
            original_url=(
                "https://indicator.example.com/read/dbd"
            ),
            normalized_url=(
                "https://indicator.example.com/read/dbd"
            ),
            title="Kasus DBD meningkat",
            content_text=(
                "Kasus DBD meningkat menjadi 42 kasus "
                "dengan 2 kematian."
            ),
            content_hash="e" * 64,
            processing_status=(
                Article.ProcessingStatus.PROCESSED
            ),
        )

        self.disease = Disease.objects.create(
            name="Demam Berdarah Dengue",
            code="dbd-indicator",
        )

        self.location = Location.objects.create(
            name="Kabupaten Bandung",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            country_code="ID",
        )

        IndicatorType.objects.create(
            code="case-increase",
            name="Peningkatan Kasus",
            category=(
                IndicatorType.Category.EPIDEMIOLOGICAL
            ),
            default_weight=2.0,
        )

        IndicatorType.objects.create(
            code="death-reported",
            name="Kematian Dilaporkan",
            category=IndicatorType.Category.IMPACT,
            default_weight=3.0,
        )

        self.fact = ArticleFact.objects.create(
            article=self.article,
            disease=self.disease,
            location=self.location,
            event_date=date(2026, 7, 28),
            case_count=42,
            death_count=2,
            trend=ArticleFact.Trend.INCREASING,
            fact_text=(
                "Kasus DBD meningkat menjadi 42 kasus "
                "dengan 2 kematian."
            ),
            confidence_score=0.90,
            extraction_method=ExtractionMethod.RULE_BASED,
            validation_status=ValidationStatus.VALIDATED,
        )

    def test_generates_case_and_death_indicators(self):
        result = generate_indicators_from_fact(
            self.fact
        )

        self.assertEqual(
            len(result.indicators_created),
            2,
        )

        self.assertEqual(
            Indicator.objects.count(),
            2,
        )

        self.assertTrue(
            Indicator.objects.filter(
                indicator_type__code="case-increase",
            ).exists()
        )

        self.assertTrue(
            Indicator.objects.filter(
                indicator_type__code="death-reported",
            ).exists()
        )

        self.assertEqual(
            IndicatorEvidence.objects.count(),
            2,
        )

    def test_unreviewed_fact_is_skipped(self):
        self.fact.validation_status = (
            ValidationStatus.UNREVIEWED
        )
        self.fact.save(
            update_fields=[
                "validation_status",
            ]
        )

        result = generate_indicators_from_fact(
            self.fact
        )

        self.assertEqual(
            len(result.indicators_created),
            0,
        )

        self.assertIn(
            "belum divalidasi",
            result.skipped_reason,
        )

    def test_generation_is_idempotent(self):
        first_result = generate_indicators_from_fact(
            self.fact
        )

        second_result = generate_indicators_from_fact(
            self.fact
        )

        self.assertEqual(
            len(first_result.indicators_created),
            2,
        )

        self.assertEqual(
            len(second_result.indicators_created),
            0,
        )

        self.assertEqual(
            len(second_result.indicators_existing),
            2,
        )

        self.assertEqual(
            Indicator.objects.count(),
            2,
        )

    def test_indicator_waits_for_analyst_review(self):
        generate_indicators_from_fact(
            self.fact
        )

        indicator = Indicator.objects.first()

        self.assertEqual(
            indicator.status,
            Indicator.Status.NEEDS_REVIEW,
        )

        self.assertTrue(
            indicator.created_by_system
        )