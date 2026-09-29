from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.articles.models import Article
from apps.assessments.models import (
    ArticleValidationAssessment,
    ArticleValidationHistory,
)
from apps.assessments.services.ai_evidence import apply_ai_evidence
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ExtractionMethod,
    ValidationStatus,
)
from apps.locations.models import Location
from apps.sources.models import Source


class BulkPreassessmentCommandTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="RRI",
            code="rri-bulk",
            domain="rri-bulk.co.id",
            base_url="https://rri-bulk.co.id/",
            source_type=Source.SourceType.GOVERNMENT,
            is_verified=True,
        )
        self.disease = Disease.objects.create(
            name="Tuberkulosis Bulk",
            code="tuberkulosis-bulk",
        )
        self.location = Location.objects.create(
            name="Kabupaten Tangerang Bulk",
            code="36.03.bulk",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            country_code="ID",
            latitude=-6.18,
            longitude=106.63,
        )

    def test_rule_based_result_validates_and_is_idempotent(self):
        article = self._article("complete")
        self._complete_evidence(article)

        call_command("bulk_validate_articles", limit=20)

        assessment = ArticleValidationAssessment.objects.get(article=article)
        article.refresh_from_db()

        self.assertEqual(
            assessment.validation_status,
            ArticleValidationAssessment.ValidationStatus.VALIDATED,
        )
        self.assertEqual(
            assessment.auto_recommendation,
            ArticleValidationAssessment
            .AutoRecommendation
            .RECOMMEND_VALIDATE,
        )
        self.assertEqual(
            assessment.auto_assessment_method,
            ArticleValidationAssessment
            .AutoAssessmentMethod
            .RULE_BASED,
        )
        self.assertIsNotNone(assessment.auto_assessed_at)
        self.assertEqual(
            article.processing_status,
            Article.ProcessingStatus.VALIDATED,
        )
        self.assertEqual(assessment.history.count(), 1)

        call_command("bulk_validate_articles", limit=20)
        self.assertEqual(assessment.history.count(), 1)

    def test_skip_ai_marks_incomplete_article_for_review(self):
        article = self._article("incomplete")

        call_command("bulk_validate_articles", limit=20, skip_ai=True)

        assessment = ArticleValidationAssessment.objects.get(article=article)
        self.assertEqual(
            assessment.validation_status,
            ArticleValidationAssessment.ValidationStatus.PENDING,
        )
        self.assertEqual(
            assessment.auto_recommendation,
            ArticleValidationAssessment.AutoRecommendation.NEEDS_REVIEW,
        )
        self.assertEqual(assessment.source_reliability, "C")
        self.assertEqual(assessment.information_credibility, 6)

    def test_ai_cannot_validate_without_linked_structured_evidence(self):
        article = self._article("ai-incomplete")
        ai_result = {
            "auto_recommendation": "recommend_validate",
            "source_reliability": "C",
            "information_credibility": 3,
            "relevance_notes": "Relevan.",
            "assessment_notes": "AI yakin.",
        }
        with patch(
            "apps.assessments.management.commands.bulk_validate_articles."
            "Command._call_ai_assessment",
            return_value=ai_result,
        ):
            call_command("bulk_validate_articles", limit=20)

        assessment = ArticleValidationAssessment.objects.get(article=article)
        article.refresh_from_db()
        self.assertEqual(assessment.validation_status, "pending")
        self.assertEqual(assessment.auto_recommendation, "needs_review")
        self.assertEqual(assessment.information_credibility, 6)
        self.assertEqual(article.processing_status, Article.ProcessingStatus.PROCESSED)

    def test_bulk_extracts_missing_fact_before_rule_assessment(self):
        article = self._article("extract-fact")
        ArticleDisease.objects.create(
            article=article, disease=self.disease, is_primary=True,
        )
        ArticleLocation.objects.create(
            article=article, location=self.location, is_primary=True,
        )
        article.content_text = "Dinas Kesehatan mencatat 42 kasus tuberkulosis."
        article.save(update_fields=["content_text"])

        call_command("bulk_validate_articles", limit=20, skip_ai=True)

        assessment = ArticleValidationAssessment.objects.get(article=article)
        self.assertTrue(ArticleFact.objects.filter(article=article, case_count=42).exists())
        self.assertEqual(assessment.auto_recommendation, "recommend_validate")

    def test_dry_run_rolls_back_reextracted_fact(self):
        article = self._article("dry-extraction")
        ArticleDisease.objects.create(
            article=article, disease=self.disease, is_primary=True,
        )
        ArticleLocation.objects.create(
            article=article, location=self.location, is_primary=True,
        )
        article.content_text = "Dinas Kesehatan mencatat 42 kasus tuberkulosis."
        article.save(update_fields=["content_text"])

        call_command("bulk_validate_articles", limit=20, dry_run=True, skip_ai=True)

        self.assertFalse(ArticleFact.objects.filter(article=article).exists())
        self.assertFalse(ArticleValidationAssessment.objects.filter(article=article).exists())

    def test_ai_structured_evidence_from_exact_quote(self):
        article = self._article("ai-quote")
        article.title = "42 kasus Tuberkulosis Bulk di Kabupaten Tangerang Bulk"
        article.save(update_fields=["title"])
        payload = {
            "disease_name": self.disease.name,
            "location_name": self.location.name,
            "case_count": 42,
            "death_count": None,
            "evidence_quote": article.title,
        }
        self.assertTrue(apply_ai_evidence(article, payload))
        self.assertTrue(ArticleFact.objects.filter(
            article=article, disease=self.disease, location=self.location,
            case_count=42,
        ).exists())

    def test_bulk_uses_verified_ai_evidence_before_final_status(self):
        article = self._article("ai-completes")
        article.title = "42 kasus Tuberkulosis Bulk di Kabupaten Tangerang Bulk"
        article.save(update_fields=["title"])
        ai_result = {
            "auto_recommendation": "recommend_validate",
            "source_reliability": "C",
            "information_credibility": 3,
            "relevance_notes": "Relevan.",
            "assessment_notes": "Kasus dilaporkan.",
            "evidence": {
                "disease_name": self.disease.name,
                "location_name": self.location.name,
                "case_count": 42,
                "death_count": None,
                "evidence_quote": article.title,
            },
        }
        # Simulasikan ekstraksi aturan yang belum dapat melengkapi data.
        with patch(
            "apps.assessments.management.commands.bulk_validate_articles."
            "reextract_article_data", return_value=False,
        ), patch(
            "apps.assessments.management.commands.bulk_validate_articles."
            "Command._call_ai_assessment", return_value=ai_result,
        ):
            call_command("bulk_validate_articles", limit=20)

        assessment = ArticleValidationAssessment.objects.get(article=article)
        self.assertEqual(assessment.validation_status, "validated")
        self.assertTrue(ArticleFact.objects.filter(article=article, case_count=42).exists())

    def test_ai_evidence_rejects_quote_absent_from_article(self):
        article = self._article("ai-invented")
        self.assertFalse(apply_ai_evidence(article, {
            "disease_name": self.disease.name,
            "location_name": self.location.name,
            "case_count": 42,
            "death_count": None,
            "evidence_quote": "42 kasus Tuberkulosis Bulk di Kabupaten Tangerang Bulk",
        }))
        self.assertFalse(ArticleFact.objects.filter(article=article).exists())

    def test_ai_evidence_does_not_reverse_analyst_rejection(self):
        article = self._article("ai-rejected")
        article.title = "42 kasus Tuberkulosis Bulk di Kabupaten Tangerang Bulk"
        article.save(update_fields=["title"])
        ArticleDisease.objects.create(
            article=article, disease=self.disease,
            validation_status=ValidationStatus.REJECTED,
        )
        self.assertFalse(apply_ai_evidence(article, {
            "disease_name": self.disease.name,
            "location_name": self.location.name,
            "case_count": 42,
            "death_count": None,
            "evidence_quote": article.title,
        }))
        self.assertFalse(ArticleFact.objects.filter(article=article).exists())

    def test_review_validated_article_recovers_word_count_without_status_change(self):
        article = self._article("legacy-seven")
        article.title = (
            "Tujuh warga terjangkit Tuberkulosis Bulk "
            "di Kabupaten Tangerang Bulk"
        )
        article.processing_status = Article.ProcessingStatus.VALIDATED
        article.save(update_fields=["title", "processing_status"])
        assessment = ArticleValidationAssessment.objects.create(
            article=article, validation_status="validated",
        )
        ArticleDisease.objects.create(
            article=article, disease=self.disease, is_primary=True,
            validation_status=ValidationStatus.VALIDATED,
        )
        ArticleLocation.objects.create(
            article=article, location=self.location, is_primary=True,
            validation_status=ValidationStatus.VALIDATED,
        )

        call_command("review_validated_article_data", limit=1, dry_run=True)
        self.assertFalse(ArticleFact.objects.filter(article=article).exists())
        call_command("review_validated_article_data", limit=1)

        self.assertTrue(ArticleFact.objects.filter(
            article=article, disease=self.disease, location=self.location,
            case_count=7,
        ).exists())
        assessment.refresh_from_db()
        article.refresh_from_db()
        self.assertEqual(assessment.validation_status, "validated")
        self.assertEqual(article.processing_status, Article.ProcessingStatus.VALIDATED)

    def test_ai_rejects_irrelevant_article(self):
        self.source.is_verified = False
        self.source.save(update_fields=["is_verified"])
        article = self._article("ai")

        ai_result = {
            "auto_recommendation": (
                ArticleValidationAssessment
                .AutoRecommendation
                .RECOMMEND_REJECT
            ),
            "source_reliability": "D",
            "information_credibility": 4,
            "relevance_notes": "Isi tidak relevan.",
            "assessment_notes": "Tidak ada bukti kejadian penyakit.",
        }

        with patch(
            "apps.assessments.management.commands.bulk_validate_articles."
            "Command._call_ai_assessment",
            return_value=ai_result,
        ):
            call_command("bulk_validate_articles", limit=20)

        assessment = ArticleValidationAssessment.objects.get(article=article)
        article.refresh_from_db()
        self.assertEqual(
            assessment.validation_status,
            ArticleValidationAssessment.ValidationStatus.REJECTED,
        )
        self.assertEqual(
            assessment.auto_recommendation,
            ArticleValidationAssessment
            .AutoRecommendation
            .RECOMMEND_REJECT,
        )
        self.assertEqual(
            assessment.auto_assessment_method,
            ArticleValidationAssessment
            .AutoAssessmentMethod
            .AI_ASSISTED,
        )
        self.assertEqual(
            article.processing_status,
            Article.ProcessingStatus.REJECTED,
        )

    def test_dry_run_does_not_create_assessment_or_history(self):
        article = self._article("dry-run")
        self._complete_evidence(article)
        output = StringIO()

        call_command(
            "bulk_validate_articles",
            dry_run=True,
            limit=20,
            stdout=output,
        )

        self.assertFalse(
            ArticleValidationAssessment.objects.filter(article=article).exists()
        )
        self.assertEqual(ArticleValidationHistory.objects.count(), 0)
        self.assertIn("DRY RUN", output.getvalue())

    def test_final_analyst_decision_is_never_overwritten(self):
        article = self._article("final")
        assessment = ArticleValidationAssessment.objects.create(
            article=article,
            validation_status=(
                ArticleValidationAssessment.ValidationStatus.VALIDATED
            ),
            source_reliability="B",
            information_credibility=2,
        )

        call_command("bulk_validate_articles", limit=20, force=True)

        assessment.refresh_from_db()
        self.assertEqual(
            assessment.validation_status,
            ArticleValidationAssessment.ValidationStatus.VALIDATED,
        )
        self.assertIsNone(assessment.auto_assessed_at)

    def _article(self, slug):
        return Article.objects.create(
            source=self.source,
            original_url=f"https://rri-bulk.co.id/{slug}",
            normalized_url=f"https://rri-bulk.co.id/{slug}",
            title=f"Artikel {slug}",
            content_text="Dinas Kesehatan mencatat kasus tuberkulosis.",
            content_hash=(slug * 64)[:64],
            published_at=timezone.now(),
            processing_status=Article.ProcessingStatus.PROCESSED,
        )

    def _complete_evidence(self, article):
        ArticleDisease.objects.create(
            article=article,
            disease=self.disease,
            mention_text="TBC",
            extraction_method=ExtractionMethod.SYSTEM,
            is_primary=True,
        )
        ArticleLocation.objects.create(
            article=article,
            location=self.location,
            mention_text="Kabupaten Tangerang",
            extraction_method=ExtractionMethod.SYSTEM,
            is_primary=True,
        )
        ArticleFact.objects.create(
            article=article,
            disease=self.disease,
            location=self.location,
            case_count=42,
            fact_text="Dinas Kesehatan mencatat 42 kasus.",
            extraction_method=ExtractionMethod.SYSTEM,
            validation_status=ValidationStatus.UNREVIEWED,
        )
