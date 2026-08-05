from datetime import date, timedelta

from django.contrib.auth import get_user_model
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
from apps.requirements.models import (
    IntelligenceRequirement,
    RequirementIndicator,
)
from apps.signals.models import (
    Signal,
    SignalArticle,
    SignalIndicator,
    SignalRequirement,
)
from apps.signals.services import (
    generate_signal_from_indicator,
)
from apps.sources.models import Source


User = get_user_model()


class SignalGenerationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="signal-analyst",
            password="test-password-123",
        )

        self.source = Source.objects.create(
            name="Media Signal",
            code="media-signal",
            domain="signal.example.com",
            base_url="https://signal.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

        self.disease, _ = Disease.objects.get_or_create(
            name="Demam Berdarah Dengue",
            defaults={"code": "dbd-signal"},
        )

        self.location = Location.objects.create(
            name="Kabupaten Bandung",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            country_code="ID",
        )

        self.indicator_type = IndicatorType.objects.create(
            code="case-increase",
            name="Peningkatan Kasus",
            category=(
                IndicatorType.Category.EPIDEMIOLOGICAL
            ),
            default_weight=2.0,
        )

        self.requirement = (
            IntelligenceRequirement.objects.create(
                code="ir-signal-001",
                title=(
                    "Peningkatan penyakit menular "
                    "berpotensi KLB"
                ),
                description=(
                    "Memantau peningkatan penyakit menular."
                ),
                requirement_type=(
                    IntelligenceRequirement.RequirementType.DISEASE_EVENT
                ),
                priority=(
                    IntelligenceRequirement.Priority.HIGH
                ),
                is_active=True,
            )
        )

        self.article = self.create_article(
            suffix="001",
        )

        self.fact = ArticleFact.objects.create(
            article=self.article,
            disease=self.disease,
            location=self.location,
            event_date=date(2026, 7, 28),
            case_count=42,
            trend=ArticleFact.Trend.INCREASING,
            fact_text=(
                "Kasus DBD meningkat menjadi 42 kasus."
            ),
            confidence_score=0.90,
            extraction_method=ExtractionMethod.RULE_BASED,
            validation_status=ValidationStatus.VALIDATED,
        )

        self.indicator = self.create_indicator(
            article=self.article,
            fact=self.fact,
            event_date=date(2026, 7, 28),
            value=42,
        )

    def create_article(
        self,
        *,
        suffix: str,
    ) -> Article:
        return Article.objects.create(
            source=self.source,
            original_url=(
                f"https://signal.example.com/read/dbd-{suffix}"
            ),
            normalized_url=(
                f"https://signal.example.com/read/dbd-{suffix}"
            ),
            title=f"Kasus DBD meningkat {suffix}",
            content_text=(
                "Kasus DBD meningkat di Kabupaten Bandung."
            ),
            content_hash=suffix.zfill(64),
            processing_status=(
                Article.ProcessingStatus.PROCESSED
            ),
        )

    def create_indicator(
        self,
        *,
        article: Article,
        fact: ArticleFact,
        event_date: date,
        value: int,
    ) -> Indicator:
        indicator = Indicator.objects.create(
            indicator_type=self.indicator_type,
            disease=self.disease,
            location=self.location,
            event_date=event_date,
            value=value,
            unit="kasus",
            direction=Indicator.Direction.INCREASING,
            summary=(
                "Terindikasi peningkatan kasus DBD "
                "di Kabupaten Bandung."
            ),
            confidence_score=0.90,
            status=Indicator.Status.VALIDATED,
            validated_by=self.user,
        )

        IndicatorEvidence.objects.create(
            indicator=indicator,
            article=article,
            article_fact=fact,
            evidence_text=fact.fact_text,
            confidence_score=0.90,
            is_primary_evidence=True,
        )

        RequirementIndicator.objects.create(
            requirement=self.requirement,
            indicator=indicator,
            relevance_score=0.90,
            relevance_reason=(
                "Penyakit dan lokasi sesuai kebutuhan."
            ),
        )

        return indicator

    def test_creates_signal_from_valid_indicator(self):
        result = generate_signal_from_indicator(
            self.indicator
        )

        self.assertTrue(result.created)
        self.assertIsNotNone(result.signal)

        self.assertEqual(
            Signal.objects.count(),
            1,
        )

        signal = Signal.objects.get()

        self.assertEqual(
            signal.primary_disease,
            self.disease,
        )

        self.assertEqual(
            signal.primary_location,
            self.location,
        )

        self.assertEqual(
            signal.status,
            Signal.Status.NEEDS_REVIEW,
        )

        self.assertEqual(
            SignalIndicator.objects.count(),
            1,
        )

        self.assertEqual(
            SignalArticle.objects.count(),
            1,
        )

        self.assertEqual(
            SignalRequirement.objects.count(),
            1,
        )

    def test_second_indicator_joins_existing_signal(self):
        generate_signal_from_indicator(
            self.indicator
        )

        second_article = self.create_article(
            suffix="002",
        )

        second_fact = ArticleFact.objects.create(
            article=second_article,
            disease=self.disease,
            location=self.location,
            event_date=(
                date(2026, 7, 28)
                + timedelta(days=3)
            ),
            case_count=50,
            trend=ArticleFact.Trend.INCREASING,
            fact_text=(
                "Kasus DBD meningkat menjadi 50 kasus."
            ),
            confidence_score=0.90,
            extraction_method=ExtractionMethod.RULE_BASED,
            validation_status=ValidationStatus.VALIDATED,
        )

        second_indicator = self.create_indicator(
            article=second_article,
            fact=second_fact,
            event_date=date(2026, 7, 31),
            value=50,
        )

        result = generate_signal_from_indicator(
            second_indicator
        )

        self.assertFalse(result.created)

        self.assertEqual(
            Signal.objects.count(),
            1,
        )

        signal = Signal.objects.get()

        self.assertEqual(
            signal.signal_indicators.count(),
            2,
        )

        self.assertEqual(
            signal.signal_articles.count(),
            2,
        )

        self.assertEqual(
            signal.event_start_date,
            date(2026, 7, 28),
        )

        self.assertEqual(
            signal.event_end_date,
            date(2026, 7, 31),
        )

    def test_indicator_without_requirement_is_skipped(self):
        self.indicator.requirement_matches.all().delete()

        result = generate_signal_from_indicator(
            self.indicator
        )

        self.assertIsNone(result.signal)

        self.assertIn(
            "kebutuhan intelijen",
            result.skipped_reason,
        )

    def test_generation_is_idempotent(self):
        first_result = generate_signal_from_indicator(
            self.indicator
        )

        second_result = generate_signal_from_indicator(
            self.indicator
        )

        self.assertTrue(first_result.created)
        self.assertFalse(second_result.created)
        self.assertFalse(
            second_result.indicator_added
        )

        self.assertEqual(
            Signal.objects.count(),
            1,
        )

        self.assertEqual(
            SignalIndicator.objects.count(),
            1,
        )
