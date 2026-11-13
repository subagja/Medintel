"""Regression: Article Validation -> Classification -> Extraction -> Intelligence gate.

No network or OpenAI calls. Read-only routing; test DB is isolated from live records.
"""
import hashlib
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.articles.models import Article
from apps.assessments.models import ArticleValidationAssessment
from apps.assessments.services.extraction_dashboard import (
    queue_rows, queue_summary, select_batch_ids, review_for_extraction,
)
from apps.entities.models import ArticleDisease, ArticleFact, ArticleLocation, Disease, ValidationStatus
from apps.locations.models import Location
from apps.signals.services.formation import evaluate_article_signal_candidate
from apps.sources.models import Source


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class WorkflowHandoffTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="02b8h-superuser", password="test", is_staff=True, is_superuser=True)
        self.client.force_login(self.user)
        self.source = Source.objects.create(
            name="Media Uji 02B8H", code="media-02b8h", domain="media02b8h.test",
            base_url="https://media02b8h.test",
            source_type=Source.SourceType.NATIONAL_MEDIA, is_verified=True)
        self.disease = Disease.objects.create(name="Polio Uji 02B8H", code="polio-02b8h")
        self.location = Location.objects.create(
            name="Kota Uji 02B8H", code="97.50",
            administrative_level=Location.AdministrativeLevel.CITY, country_code="ID")

    def article(self, slug, category=None, validation=None, numeric=False):
        metadata = {}
        if category:
            metadata["information_type_review"] = {
                "category": category, "source": "master_admin_ai_approval",
                "reason": "Kategori berdasarkan isi artikel"}
        article = Article.objects.create(
            source=self.source,
            original_url=f"https://media02b8h.test/{slug}",
            normalized_url=f"https://media02b8h.test/{slug}",
            title=f"Artikel {slug}",
            content_text=f"Laporan penyakit di Kota Uji {slug}.",
            content_hash=hashlib.sha256(slug.encode()).hexdigest(),
            processing_status=Article.ProcessingStatus.PROCESSED,
            raw_metadata=metadata)
        if validation:
            ArticleValidationAssessment.objects.create(
                article=article, validation_status=validation)
        if numeric:
            ArticleDisease.objects.create(
                article=article, disease=self.disease, is_primary=True,
                validation_status=ValidationStatus.VALIDATED)
            ArticleLocation.objects.create(
                article=article, location=self.location, is_primary=True,
                validation_status=ValidationStatus.VALIDATED)
            ArticleFact.objects.create(
                article=article, disease=self.disease, location=self.location,
                case_count=12, event_date=date(2026, 11, 10),
                fact_text="12 kasus polio di Kota Uji.",
                validation_status=ValidationStatus.VALIDATED)
        return article

    def _row(self, article):
        return next(r for r in queue_rows() if r["article"].pk == article.pk)

    def test_unverified_case_without_number_is_monitored_in_extraction(self):
        article = self.article("case-no-number", "case_event", "validated")
        row = self._row(article)
        self.assertTrue(row["verification_needed"])
        self.assertFalse(row["analysis_ready"])
        self.assertEqual(queue_summary(queue_rows())["verification_needed"], 1)
        response = self.client.get(reverse("dashboard:entity-extraction"), {"scope": "verification"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["extraction_page"].paginator.count, 1)

    def test_context_without_case_number_exits_pending_bucket(self):
        article = self.article("prevention", "prevention_response", "validated")
        row = self._row(article)
        summary = queue_summary(queue_rows())
        self.assertEqual(summary["contextual_articles"], 1)
        self.assertEqual(summary["incomplete"], 0)
        self.assertFalse(row["verification_needed"])
        self.assertFalse(row["analysis_ready"])

    @patch("apps.assessments.services.extraction_dashboard.reextract_article_data")
    def test_old_manual_batch_does_not_turn_context_into_cases(self, extract):
        article = self.article("vaccination", "prevention_response", "validated")
        result = review_for_extraction(article.pk, with_ai=False)
        self.assertTrue(result["skipped"])
        extract.assert_not_called()

    def test_reviewed_case_with_pending_validation_cannot_enter_analysis(self):
        article = self.article("numerical-pending", "case_event", "pending", numeric=True)
        self.assertFalse(self._row(article)["analysis_ready"])
        self.assertEqual(queue_summary(queue_rows())["complete"], 1)
        self.assertEqual(queue_summary(queue_rows())["analysis_ready"], 0)

    def test_reviewed_case_with_validated_article_is_analysis_ready(self):
        article = self.article("numerical-validated", "case_event", "validated", numeric=True)
        self.assertTrue(self._row(article)["analysis_ready"])
        self.assertEqual(queue_summary(queue_rows())["analysis_ready"], 1)

    def test_context_numeric_fact_is_not_a_case(self):
        article = self.article("context-with-fact", "risk_indicator", "validated", numeric=True)
        self.assertFalse(self._row(article)["analysis_ready"])
        summary = queue_summary(queue_rows())
        self.assertEqual((summary["contextual_articles"], summary["complete"]), (1, 0))

    def test_rejected_validation_excludes_article_from_extraction(self):
        article = self.article("validation-rejected", "case_event", "rejected")
        row = self._row(article)
        self.assertEqual(row["state"], "rejected")
        self.assertEqual(queue_summary(queue_rows())["rejected"], 1)
        self.assertNotIn(article.pk, select_batch_ids(10, scope="incomplete"))

    def test_unconfirmed_category_does_not_form_case_signal(self):
        article = self.article("non-case", "prevention_response", "validated", numeric=True)
        candidate = evaluate_article_signal_candidate(article)
        self.assertFalse(candidate.is_ready)
        self.assertTrue(any("kategori" in text.lower() for text in candidate.blockers))

    def test_four_counts_partition_all_articles_without_double_counting(self):
        self.article("case", "case_event", "validated")
        self.article("context", "risk_indicator", "pending")
        self.article("complete", "case_event", "validated", numeric=True)
        self.article("rejected", "prevention_response", "rejected")
        counts = queue_summary(queue_rows())
        self.assertEqual(counts["total"], 4)
        self.assertEqual(4, sum(counts[key] for key in (
            "incomplete", "complete", "contextual_articles", "rejected")))
        self.assertEqual(counts["verification_needed"], 1)
        self.assertEqual(counts["analysis_ready"], 1)
