from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.articles.models import Article
from apps.assessments.models import ArticleValidationAssessment
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ExtractionMethod,
    ExtractionReviewLog,
    ValidationStatus,
)
from apps.locations.models import Location
from apps.entities.services.review import (
    set_primary_article_disease,
)
from apps.signals.services import (
    evaluate_article_signal_candidate,
)
from apps.sources.models import Source


User = get_user_model()


class PrimaryArticleDiseaseReviewTests(TestCase):
    def setUp(self):
        self.analyst = User.objects.create_user(
            username="disease-analyst",
            password="test-password-123",
        )
        self.source = Source.objects.create(
            name="Media Penyakit Uji",
            code="media-penyakit-uji",
            domain="penyakit.example.com",
            base_url="https://penyakit.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )
        self.article = Article.objects.create(
            source=self.source,
            original_url=(
                "https://penyakit.example.com/tbc-tangerang"
            ),
            normalized_url=(
                "https://penyakit.example.com/tbc-tangerang"
            ),
            title="Penderita TBC di Tangerang 5.101 kasus",
            content_text=(
                "Kabupaten Tangerang mencatat 5.101 kasus TBC."
            ),
            content_hash="d" * 64,
            processing_status=(
                Article.ProcessingStatus.VALIDATED
            ),
        )
        self.tuberculosis = Disease.objects.create(
            name="Tuberkulosis Primary Disease Test",
            code="tuberkulosis-primary-disease-test",
        )
        self.dengue = Disease.objects.create(
            name="DBD Primary Disease Test",
            code="dbd-primary-disease-test",
        )
        self.tb_relation = ArticleDisease.objects.create(
            article=self.article,
            disease=self.tuberculosis,
            mention_text="TBC",
            is_primary=False,
            validation_status=ValidationStatus.VALIDATED,
        )
        self.dengue_relation = ArticleDisease.objects.create(
            article=self.article,
            disease=self.dengue,
            mention_text="DBD",
            is_primary=True,
            validation_status=ValidationStatus.VALIDATED,
        )
        self.location = Location.objects.create(
            name="Kabupaten Tangerang Disease Test",
            code="36.03-disease-test",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            country_code="ID",
        )
        ArticleLocation.objects.create(
            article=self.article,
            location=self.location,
            is_primary=True,
            validation_status=ValidationStatus.CORRECTED,
        )
        self.fact = ArticleFact.objects.create(
            article=self.article,
            disease=self.tuberculosis,
            location=self.location,
            event_date=date(2026, 8, 4),
            case_count=5101,
            fact_text="Terdapat 5.101 kasus TBC.",
            extraction_method=ExtractionMethod.RULE_BASED,
            validation_status=ValidationStatus.VALIDATED,
        )
        ArticleValidationAssessment.objects.create(
            article=self.article,
            validation_status=(
                ArticleValidationAssessment
                .ValidationStatus
                .VALIDATED
            ),
            source_reliability=(
                ArticleValidationAssessment.SourceReliability.B
            ),
            information_credibility=(
                ArticleValidationAssessment
                .InformationCredibility
                .PROBABLY_TRUE
            ),
            evaluated_by=self.analyst,
        )

    def test_sets_one_primary_and_keeps_context_diseases(self):
        result = set_primary_article_disease(
            article=self.article,
            disease=self.tuberculosis,
            reviewer=self.analyst,
            notes="Fakta 5.101 kasus secara tegas merujuk TBC.",
        )

        self.tb_relation.refresh_from_db()
        self.dengue_relation.refresh_from_db()
        self.fact.refresh_from_db()

        self.assertTrue(self.tb_relation.is_primary)
        self.assertEqual(
            self.tb_relation.validation_status,
            ValidationStatus.CORRECTED,
        )
        self.assertFalse(self.dengue_relation.is_primary)
        self.assertEqual(
            self.dengue_relation.validation_status,
            ValidationStatus.CORRECTED,
        )
        self.assertEqual(self.fact.disease, self.tuberculosis)
        self.assertEqual(
            ArticleDisease.objects.filter(
                article=self.article,
                is_primary=True,
            ).count(),
            1,
        )
        self.assertEqual(result.demoted_count, 1)
        self.assertEqual(result.context_count, 1)
        self.assertTrue(result.changed)
        self.assertEqual(
            ExtractionReviewLog.objects.filter(
                object_type=(
                    ExtractionReviewLog
                    .ObjectType
                    .ARTICLE_DISEASE
                ),
                action=ExtractionReviewLog.Action.CORRECT,
            ).count(),
            2,
        )

        candidate = evaluate_article_signal_candidate(
            self.article
        )
        self.assertTrue(candidate.is_ready)
        self.assertEqual(
            candidate.primary_disease,
            self.tuberculosis,
        )
        self.assertEqual(candidate.fact, self.fact)

    def test_repeating_same_decision_does_not_duplicate_history(self):
        notes = "Fakta 5.101 kasus secara tegas merujuk TBC."

        set_primary_article_disease(
            article=self.article,
            disease=self.tuberculosis,
            reviewer=self.analyst,
            notes=notes,
        )
        history_count = ExtractionReviewLog.objects.count()

        result = set_primary_article_disease(
            article=self.article,
            disease=self.tuberculosis,
            reviewer=self.analyst,
            notes=notes,
        )

        self.assertFalse(result.changed)
        self.assertEqual(
            ExtractionReviewLog.objects.count(),
            history_count,
        )

    def test_can_add_disease_missing_from_extraction(self):
        malaria = Disease.objects.create(
            name="Malaria Primary Disease Test",
            code="malaria-primary-disease-test",
        )

        result = set_primary_article_disease(
            article=self.article,
            disease=malaria,
            reviewer=self.analyst,
            notes="Isi artikel yang diperiksa merujuk malaria.",
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

    def test_requires_notes_and_authenticated_reviewer(self):
        with self.assertRaises(ValidationError):
            set_primary_article_disease(
                article=self.article,
                disease=self.tuberculosis,
                reviewer=self.analyst,
                notes="",
            )

        with self.assertRaises(ValidationError):
            set_primary_article_disease(
                article=self.article,
                disease=self.tuberculosis,
                reviewer=None,
                notes="Penetapan penyakit utama.",
            )

    def test_rejects_inactive_disease(self):
        self.tuberculosis.is_active = False
        self.tuberculosis.save(update_fields=["is_active"])

        with self.assertRaises(ValidationError):
            set_primary_article_disease(
                article=self.article,
                disease=self.tuberculosis,
                reviewer=self.analyst,
                notes="Penetapan penyakit utama.",
            )

    def test_view_records_primary_disease_and_returns_to_tab(self):
        self.client.force_login(self.analyst)

        response = self.client.post(
            reverse("dashboard:article-validation"),
            {
                "article_id": str(self.article.id),
                "action": "correct_primary_disease",
                "primary_disease": str(self.tuberculosis.id),
                "disease_correction_notes": (
                    "Judul dan angka kasus merujuk TBC."
                ),
                # Field milik tab Lokasi Utama berada dalam form HTML
                # yang sama dan boleh kosong tanpa menimpa catatan penyakit.
                "correction_notes": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("tab=disease", response.url)
        self.tb_relation.refresh_from_db()
        self.assertTrue(self.tb_relation.is_primary)

    def test_multi_action_form_uses_server_side_validation(self):
        self.client.force_login(self.analyst)

        response = self.client.get(
            reverse("dashboard:article-validation"),
            {"article": str(self.article.id), "tab": "disease"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<form method="post" novalidate>',
        )
