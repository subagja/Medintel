from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
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
    IndicatorReviewLog,
    IndicatorType,
)
from apps.indicators.services import (
    correct_indicator,
    indicator_is_eligible_for_signal,
    reject_indicator,
    validate_indicator,
)
from apps.sources.models import Source


User = get_user_model()


class IndicatorReviewTests(TestCase):
    def setUp(self):
        self.reviewer = User.objects.create_user(
            username="indicator-analyst",
            password="test-password-123",
        )

        self.source = Source.objects.create(
            name="Media Review Indikator",
            code="media-review-indikator",
            domain="indicator-review.example.com",
            base_url="https://indicator-review.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

        self.article = Article.objects.create(
            source=self.source,
            original_url=(
                "https://indicator-review.example.com/read/dbd"
            ),
            normalized_url=(
                "https://indicator-review.example.com/read/dbd"
            ),
            title="Kasus DBD meningkat",
            content_text=(
                "Kasus DBD meningkat menjadi 42 kasus."
            ),
            content_hash="f" * 64,
            processing_status=(
                Article.ProcessingStatus.PROCESSED
            ),
        )

        self.disease, _ = Disease.objects.get_or_create(
            name="Demam Berdarah Dengue",
            defaults={"code": "dbd-indicator-review"},
        )

        self.location = Location.objects.create(
            name="Kabupaten Bandung",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            country_code="ID",
        )

        self.alternative_location = Location.objects.create(
            name="Kota Bandung",
            administrative_level=(
                Location.AdministrativeLevel.CITY
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

        self.indicator = Indicator.objects.create(
            indicator_type=self.indicator_type,
            disease=self.disease,
            location=self.location,
            event_date=date(2026, 7, 28),
            value=42,
            unit="kasus",
            direction=Indicator.Direction.INCREASING,
            summary=(
                "Terindikasi peningkatan kasus DBD "
                "di Kabupaten Bandung."
            ),
            confidence_score=0.90,
            status=Indicator.Status.NEEDS_REVIEW,
            created_by_system=True,
        )

        IndicatorEvidence.objects.create(
            indicator=self.indicator,
            article=self.article,
            article_fact=self.fact,
            evidence_text=self.fact.fact_text,
            confidence_score=0.90,
            is_primary_evidence=True,
        )

    def test_validates_indicator(self):
        validate_indicator(
            indicator=self.indicator,
            reviewer=self.reviewer,
            notes="Indikator sesuai dengan bukti artikel.",
        )

        self.indicator.refresh_from_db()

        self.assertEqual(
            self.indicator.status,
            Indicator.Status.VALIDATED,
        )

        self.assertEqual(
            self.indicator.validated_by,
            self.reviewer,
        )

        self.assertIsNotNone(
            self.indicator.validated_at
        )

        review = IndicatorReviewLog.objects.get()

        self.assertEqual(
            review.action,
            IndicatorReviewLog.Action.VALIDATE,
        )

    def test_corrects_indicator(self):
        correct_indicator(
            indicator=self.indicator,
            reviewer=self.reviewer,
            corrections={
                "location": self.alternative_location,
                "value": 45,
                "summary": (
                    "Terindikasi peningkatan kasus DBD "
                    "di Kota Bandung."
                ),
            },
            notes=(
                "Lokasi dan jumlah kasus dikoreksi "
                "sesuai isi artikel."
            ),
        )

        self.indicator.refresh_from_db()

        self.assertEqual(
            self.indicator.location,
            self.alternative_location,
        )

        self.assertEqual(
            self.indicator.value,
            45,
        )

        self.assertEqual(
            self.indicator.status,
            Indicator.Status.CORRECTED,
        )

        review = IndicatorReviewLog.objects.get()

        self.assertNotEqual(
            review.before_data["location_id"],
            review.after_data["location_id"],
        )

    def test_reject_requires_notes(self):
        with self.assertRaises(ValidationError):
            reject_indicator(
                indicator=self.indicator,
                reviewer=self.reviewer,
                notes="",
            )

    def test_rejects_indicator(self):
        reject_indicator(
            indicator=self.indicator,
            reviewer=self.reviewer,
            notes=(
                "Peningkatan kasus tidak didukung "
                "oleh konteks artikel."
            ),
        )

        self.indicator.refresh_from_db()

        self.assertEqual(
            self.indicator.status,
            Indicator.Status.REJECTED,
        )

    def test_unreviewed_indicator_is_not_signal_eligible(self):
        eligible, reason = (
            indicator_is_eligible_for_signal(
                self.indicator
            )
        )

        self.assertFalse(eligible)

        self.assertIn(
            "belum divalidasi",
            reason,
        )

    def test_validated_indicator_is_signal_eligible(self):
        validate_indicator(
            indicator=self.indicator,
            reviewer=self.reviewer,
            notes="Indikator valid.",
        )

        eligible, reason = (
            indicator_is_eligible_for_signal(
                self.indicator
            )
        )

        self.assertTrue(eligible)
        self.assertEqual(reason, "")
