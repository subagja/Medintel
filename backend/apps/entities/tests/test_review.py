from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.articles.models import Article
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ExtractionMethod,
    ExtractionReviewLog,
    Location,
    ValidationStatus,
)
from apps.entities.services import (
    correct_article_fact,
    correct_article_location,
    reject_article_disease,
    validate_article_disease,
    validate_article_fact,
)
from apps.sources.models import Source


User = get_user_model()


class ExtractionReviewTests(TestCase):
    def setUp(self):
        self.reviewer = User.objects.create_user(
            username="analyst",
            password="test-password-123",
        )

        self.source = Source.objects.create(
            name="Media Review",
            code="media-review",
            domain="review.example.com",
            base_url="https://review.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

        self.article = Article.objects.create(
            source=self.source,
            original_url=(
                "https://review.example.com/read/dbd"
            ),
            normalized_url=(
                "https://review.example.com/read/dbd"
            ),
            title="Kasus DBD meningkat",
            content_text=(
                "Kasus DBD meningkat menjadi 42 kasus."
            ),
            content_hash="d" * 64,
            processing_status=(
                Article.ProcessingStatus.PROCESSED
            ),
        )

        self.dbd = Disease.objects.create(
            name="Demam Berdarah Dengue",
            code="dbd-review",
        )

        self.polio = Disease.objects.create(
            name="Polio",
            code="polio-review",
        )

        self.indonesia = Location.objects.create(
            name="Indonesia",
            administrative_level=(
                Location.AdministrativeLevel.COUNTRY
            ),
            country_code="ID",
        )

        self.jawa_barat = Location.objects.create(
            name="Jawa Barat",
            administrative_level=(
                Location.AdministrativeLevel.PROVINCE
            ),
            parent=self.indonesia,
            country_code="ID",
        )

        self.bandung = Location.objects.create(
            name="Kabupaten Bandung",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            parent=self.jawa_barat,
            country_code="ID",
        )

        self.disease_relation = ArticleDisease.objects.create(
            article=self.article,
            disease=self.dbd,
            mention_text="DBD",
            confidence_score=0.90,
            extraction_method=ExtractionMethod.RULE_BASED,
        )

        self.location_relation = ArticleLocation.objects.create(
            article=self.article,
            location=self.jawa_barat,
            mention_text="Bandung",
            confidence_score=0.70,
            extraction_method=ExtractionMethod.RULE_BASED,
        )

        self.fact = ArticleFact.objects.create(
            article=self.article,
            disease=self.dbd,
            location=self.bandung,
            event_date=date(2026, 7, 28),
            case_count=40,
            trend=ArticleFact.Trend.INCREASING,
            fact_text="Kasus meningkat menjadi 42 kasus.",
            confidence_score=0.80,
            extraction_method=ExtractionMethod.RULE_BASED,
        )

    def test_validates_article_disease(self):
        validate_article_disease(
            relation=self.disease_relation,
            reviewer=self.reviewer,
            notes="Penyakit sesuai isi artikel.",
        )

        self.disease_relation.refresh_from_db()

        self.assertEqual(
            self.disease_relation.validation_status,
            ValidationStatus.VALIDATED,
        )

        self.assertEqual(
            self.disease_relation.validated_by,
            self.reviewer,
        )

        self.assertEqual(
            ExtractionReviewLog.objects.count(),
            1,
        )

    def test_rejection_requires_reason(self):
        with self.assertRaises(ValidationError):
            reject_article_disease(
                relation=self.disease_relation,
                reviewer=self.reviewer,
                notes="",
            )

    def test_corrects_article_location(self):
        correct_article_location(
            relation=self.location_relation,
            location=self.bandung,
            reviewer=self.reviewer,
            notes=(
                "Penyebutan Bandung merujuk "
                "Kabupaten Bandung."
            ),
        )

        self.location_relation.refresh_from_db()

        self.assertEqual(
            self.location_relation.location,
            self.bandung,
        )

        self.assertEqual(
            self.location_relation.validation_status,
            ValidationStatus.CORRECTED,
        )

        review = ExtractionReviewLog.objects.get()

        self.assertEqual(
            review.action,
            ExtractionReviewLog.Action.CORRECT,
        )

        self.assertNotEqual(
            review.before_data["location_id"],
            review.after_data["location_id"],
        )

    def test_corrects_article_fact(self):
        correct_article_fact(
            fact=self.fact,
            reviewer=self.reviewer,
            corrections={
                "case_count": 42,
                "death_count": 2,
            },
            notes="Jumlah kasus dan kematian dikoreksi.",
        )

        self.fact.refresh_from_db()

        self.assertEqual(self.fact.case_count, 42)
        self.assertEqual(self.fact.death_count, 2)

        self.assertEqual(
            self.fact.validation_status,
            ValidationStatus.CORRECTED,
        )

    def test_validates_article_fact(self):
        validate_article_fact(
            fact=self.fact,
            reviewer=self.reviewer,
            notes="Fakta sesuai artikel.",
        )

        self.fact.refresh_from_db()

        self.assertEqual(
            self.fact.validation_status,
            ValidationStatus.VALIDATED,
        )