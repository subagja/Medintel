from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.articles.models import Article
from apps.entities.models import (
    ArticleFact,
    ArticleLocation,
    ExtractionMethod,
    ExtractionReviewLog,
    Location,
    ValidationStatus,
)
from apps.entities.services.review import (
    set_primary_article_location,
)
from apps.sources.models import Source


User = get_user_model()


class PrimaryArticleLocationReviewTests(TestCase):
    def setUp(self):
        self.analyst = User.objects.create_user(
            username="location-analyst",
            password="test-password-123",
        )

        self.source = Source.objects.create(
            name="Media Lokasi Uji",
            code="media-lokasi-uji",
            domain="lokasi.example.com",
            base_url="https://lokasi.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

        self.article = Article.objects.create(
            source=self.source,
            original_url=(
                "https://lokasi.example.com/tbc-tangerang"
            ),
            normalized_url=(
                "https://lokasi.example.com/tbc-tangerang"
            ),
            title="Penderita TBC di Tangerang 5.101 kasus",
            content_text=(
                "Kabupaten Tangerang mencatat 5.101 kasus TBC."
            ),
            content_hash="l" * 64,
            processing_status=(
                Article.ProcessingStatus.PROCESSED
            ),
        )

        self.province = Location.objects.create(
            name="Banten",
            code="36",
            administrative_level=(
                Location.AdministrativeLevel.PROVINCE
            ),
            country_code="ID",
        )

        self.regency = Location.objects.create(
            name="Kabupaten Tangerang",
            code="36.03",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            parent=self.province,
            country_code="ID",
        )

        self.city = Location.objects.create(
            name="Kota Tangerang",
            code="36.71",
            administrative_level=(
                Location.AdministrativeLevel.CITY
            ),
            parent=self.province,
            country_code="ID",
        )

        self.province_relation = ArticleLocation.objects.create(
            article=self.article,
            location=self.province,
            mention_text="Banten",
            is_primary=False,
        )

        self.regency_relation = ArticleLocation.objects.create(
            article=self.article,
            location=self.regency,
            mention_text="Kabupaten Tangerang",
            is_primary=False,
        )

        self.city_relation = ArticleLocation.objects.create(
            article=self.article,
            location=self.city,
            mention_text="Tangerang",
            is_primary=True,
        )

        self.fact = ArticleFact.objects.create(
            article=self.article,
            location=self.city,
            case_count=5101,
            fact_text="Terdapat 5.101 kasus TBC.",
        )

    def test_sets_one_primary_and_keeps_context_locations(self):
        result = set_primary_article_location(
            article=self.article,
            location=self.regency,
            reviewer=self.analyst,
            notes=(
                "Angka kasus merujuk Kabupaten Tangerang."
            ),
        )

        self.regency_relation.refresh_from_db()
        self.city_relation.refresh_from_db()
        self.province_relation.refresh_from_db()
        self.fact.refresh_from_db()

        self.assertTrue(self.regency_relation.is_primary)
        self.assertEqual(
            self.regency_relation.validation_status,
            ValidationStatus.CORRECTED,
        )
        self.assertFalse(self.city_relation.is_primary)
        self.assertEqual(
            self.city_relation.validation_status,
            ValidationStatus.CORRECTED,
        )
        self.assertFalse(self.province_relation.is_primary)
        self.assertEqual(
            ArticleLocation.objects.filter(
                article=self.article,
            ).count(),
            3,
        )
        self.assertEqual(self.fact.location, self.city)
        self.assertEqual(result.demoted_count, 1)
        self.assertEqual(result.context_count, 2)
        self.assertTrue(result.changed)

        self.assertEqual(
            ExtractionReviewLog.objects.filter(
                object_type=(
                    ExtractionReviewLog
                    .ObjectType
                    .ARTICLE_LOCATION
                ),
                action=ExtractionReviewLog.Action.CORRECT,
            ).count(),
            2,
        )

    def test_repeating_same_decision_does_not_duplicate_history(self):
        notes = "Angka kasus merujuk Kabupaten Tangerang."

        set_primary_article_location(
            article=self.article,
            location=self.regency,
            reviewer=self.analyst,
            notes=notes,
        )

        history_count = ExtractionReviewLog.objects.count()

        result = set_primary_article_location(
            article=self.article,
            location=self.regency,
            reviewer=self.analyst,
            notes=notes,
        )

        self.assertFalse(result.changed)
        self.assertEqual(
            ExtractionReviewLog.objects.count(),
            history_count,
        )

    def test_can_add_location_missing_from_extraction(self):
        new_location = Location.objects.create(
            name="Kabupaten Serang",
            code="36.04",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            parent=self.province,
            country_code="ID",
        )

        result = set_primary_article_location(
            article=self.article,
            location=new_location,
            reviewer=self.analyst,
            notes="Isi artikel merujuk Kabupaten Serang.",
        )

        self.assertTrue(result.created)
        self.assertEqual(
            result.relation.extraction_method,
            ExtractionMethod.MANUAL,
        )
        self.assertTrue(result.relation.is_primary)
        self.assertEqual(
            result.relation.validation_status,
            ValidationStatus.CORRECTED,
        )
        self.assertEqual(
            ArticleLocation.objects.filter(
                article=self.article,
                is_primary=True,
            ).count(),
            1,
        )

    def test_requires_notes_and_authenticated_reviewer(self):
        with self.assertRaises(ValidationError):
            set_primary_article_location(
                article=self.article,
                location=self.regency,
                reviewer=self.analyst,
                notes="",
            )

        with self.assertRaises(ValidationError):
            set_primary_article_location(
                article=self.article,
                location=self.regency,
                reviewer=None,
                notes="Koreksi lokasi.",
            )

    def test_rejects_inactive_location(self):
        self.regency.is_active = False
        self.regency.save(update_fields=["is_active"])

        with self.assertRaises(ValidationError):
            set_primary_article_location(
                article=self.article,
                location=self.regency,
                reviewer=self.analyst,
                notes="Koreksi lokasi.",
            )
