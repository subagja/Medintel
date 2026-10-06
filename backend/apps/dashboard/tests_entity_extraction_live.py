import hashlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.articles.models import Article
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
)
from apps.locations.models import Location
from apps.sources.models import Source


User = get_user_model()


class EntityExtractionLiveStatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="entity-extraction-live",
            password="test-password-123",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.source = Source.objects.create(
            name="Media Ekstraksi Live",
            code="media-ekstraksi-live",
            domain="ekstraksi-live.example.com",
            base_url="https://ekstraksi-live.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )
        self.disease = Disease.objects.create(
            name="Penyakit Uji Ekstraksi",
            code="penyakit-uji-ekstraksi",
        )
        self.location = Location.objects.create(
            name="Lokasi Uji Ekstraksi",
            code="99.99",
            administrative_level=Location.AdministrativeLevel.CITY,
        )

    def _create_article(self, slug):
        return Article.objects.create(
            source=self.source,
            original_url=f"https://ekstraksi-live.example.com/{slug}",
            normalized_url=f"https://ekstraksi-live.example.com/{slug}",
            title=f"Artikel {slug}",
            content_text=f"Isi artikel {slug}.",
            content_hash=hashlib.sha256(slug.encode()).hexdigest(),
            processing_status=Article.ProcessingStatus.PROCESSED,
        )

    def test_incomplete_count_uses_entity_completeness(self):
        complete = self._create_article("lengkap")
        self._create_article("belum-lengkap")

        ArticleDisease.objects.create(
            article=complete,
            disease=self.disease,
        )
        ArticleLocation.objects.create(
            article=complete,
            location=self.location,
            is_primary=True,
        )
        ArticleFact.objects.create(
            article=complete,
            disease=self.disease,
            location=self.location,
            case_count=10,
            fact_text="Sepuluh kasus ditemukan di lokasi uji.",
        )

        response = self.client.get(
            reverse("dashboard:entity-extraction-status")
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["total_articles"], 2)
        self.assertEqual(payload["summary"]["pending_count"], 1)
        self.assertEqual(payload["summary"]["with_disease"], 1)
        self.assertEqual(payload["summary"]["with_location"], 1)
        self.assertEqual(payload["summary"]["with_fact"], 1)

    def test_status_endpoint_returns_cached_batch_progress(self):
        batch_id = "batch-test-live"
        cache.set(
            f"entity-extraction-batch:{batch_id}",
            {
                "state": "running",
                "total": 50,
                "processed": 12,
                "failed": 1,
            },
            timeout=60,
        )

        response = self.client.get(
            reverse("dashboard:entity-extraction-status"),
            {"batch": batch_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["batch"],
            {
                "state": "running",
                "total": 50,
                "processed": 12,
                "failed": 1,
            },
        )


    def test_empty_fact_does_not_count_as_numeric(self):
        article = self._create_article("empty-fact")
        ArticleFact.objects.create(article=article, fact_text="Tanpa angka kasus")
        payload = self.client.get(reverse("dashboard:entity-extraction-status")).json()
        self.assertEqual(payload["summary"]["with_fact"], 0)
        self.assertEqual(payload["summary"]["pending_count"], 1)

    def test_unlinked_numeric_fact_still_needs_completion(self):
        article = self._create_article("unlinked-fact")
        ArticleDisease.objects.create(article=article, disease=self.disease)
        ArticleLocation.objects.create(article=article, location=self.location, is_primary=True)
        ArticleFact.objects.create(article=article, case_count=0, fact_text="Tidak ada kasus")
        payload = self.client.get(reverse("dashboard:entity-extraction-status")).json()
        self.assertEqual(payload["summary"]["with_fact"], 1)
        self.assertEqual(payload["summary"]["complete_count"], 0)
        self.assertEqual(payload["summary"]["pending_count"], 1)

    def test_rejected_evidence_is_not_counted(self):
        article = self._create_article("rejected-fact")
        ArticleFact.objects.create(article=article, case_count=10, validation_status="rejected")
        payload = self.client.get(reverse("dashboard:entity-extraction-status")).json()
        self.assertEqual(payload["summary"]["with_fact"], 0)

    def test_batch_moves_to_unattempted_article(self):
        from apps.assessments.services.extraction_dashboard import select_batch_ids, review_for_extraction
        first = self._create_article("first-attempt")
        second = self._create_article("next-attempt")
        self.assertEqual(select_batch_ids(1, scope="unknown"), [first.pk])
        with patch("apps.assessments.services.extraction_dashboard.reextract_article_data", return_value=False):
            result = review_for_extraction(first.pk)
        self.assertFalse(result["complete"])
        self.assertEqual(select_batch_ids(1, scope="unknown"), [second.pk])

    def test_review_preserves_validated_status_and_assessment(self):
        from apps.assessments.models import ArticleValidationAssessment
        from apps.assessments.services.extraction_dashboard import review_for_extraction
        article = self._create_article("validated-incomplete")
        article.processing_status = Article.ProcessingStatus.VALIDATED
        article.save()
        assessment = ArticleValidationAssessment.objects.create(article=article, validation_status="validated")
        with patch("apps.assessments.services.extraction_dashboard.reextract_article_data", return_value=False):
            review_for_extraction(article.pk)
        article.refresh_from_db()
        assessment.refresh_from_db()
        self.assertEqual(article.processing_status, Article.ProcessingStatus.VALIDATED)
        self.assertEqual(assessment.validation_status, "validated")

    def test_force_does_not_select_rejected_articles(self):
        from apps.assessments.services.extraction_dashboard import select_batch_ids
        article = self._create_article("rejected-article")
        article.processing_status = Article.ProcessingStatus.REJECTED
        article.save()
        self.assertEqual(select_batch_ids(50, force=True), [])

    def test_review_reports_becoming_complete(self):
        from apps.assessments.services.extraction_dashboard import review_for_extraction
        article = self._create_article("becoming-complete")
        def extract(obj, **kwargs):
            ArticleDisease.objects.create(article=obj, disease=self.disease)
            ArticleLocation.objects.create(article=obj, location=self.location, is_primary=True)
            ArticleFact.objects.create(article=obj, disease=self.disease, location=self.location,
                                       case_count=10, fact_text="Sepuluh pasien di lokasi uji")
            return True
        with patch("apps.assessments.services.extraction_dashboard.reextract_article_data", side_effect=extract):
            result = review_for_extraction(article.pk)
        self.assertTrue(result["changed"])
        self.assertTrue(result["became_complete"])
        self.assertEqual(result["missing"], [])

    def test_ai_failure_keeps_rule_results_and_reports_failure(self):
        from apps.assessments.services.extraction_dashboard import review_for_extraction
        article = self._create_article("ai-failed")
        with patch("apps.assessments.services.extraction_dashboard.reextract_article_data", return_value=False), \
             patch("apps.assessments.management.commands.bulk_validate_articles.Command._call_ai_assessment",
                   side_effect=RuntimeError("Mock timeout")):
            result = review_for_extraction(article.pk, with_ai=True)
        self.assertTrue(result["ai_failed"])
        self.assertFalse(result["complete"])
        article.refresh_from_db()
        self.assertIn("extraction_review_attempt_at", article.raw_metadata)

    def test_multiple_primary_locations_remain_incomplete(self):
        article = self._create_article("ambiguous-primary")
        other = Location.objects.create(name="Lokasi Kedua", code="99.98",
                                         administrative_level=Location.AdministrativeLevel.CITY)
        ArticleDisease.objects.create(article=article, disease=self.disease)
        for loc in (self.location, other):
            ArticleLocation.objects.create(article=article, location=loc, is_primary=True)
        ArticleFact.objects.create(article=article, disease=self.disease, location=self.location,
                                   case_count=10, fact_text="Sepuluh kasus")
        payload = self.client.get(reverse("dashboard:entity-extraction-status")).json()
        self.assertEqual(payload["summary"]["complete_count"], 0)
        self.assertEqual(payload["summary"]["pending_count"], 1)

    def test_ai_evidence_is_applied_without_changing_validation(self):
        from apps.assessments.services.extraction_dashboard import review_for_extraction
        article = self._create_article("ai-completion")
        def persist(obj, evidence):
            ArticleDisease.objects.create(article=obj, disease=self.disease)
            ArticleLocation.objects.create(article=obj, location=self.location, is_primary=True)
            ArticleFact.objects.create(article=obj, disease=self.disease, location=self.location,
                                       case_count=10, fact_text="Sepuluh kasus")
        with patch("apps.assessments.services.extraction_dashboard.reextract_article_data", return_value=False), \
             patch("apps.assessments.management.commands.bulk_validate_articles.Command._call_ai_assessment",
                   return_value={"auto_recommendation": "recommend_reject", "evidence": {"case_count": 10}}), \
             patch("apps.assessments.services.extraction_dashboard.apply_ai_evidence", side_effect=persist):
            result = review_for_extraction(article.pk, with_ai=True)
        article.refresh_from_db()
        self.assertEqual(article.processing_status, Article.ProcessingStatus.PROCESSED)
        self.assertTrue(result["became_complete"])


class ProcessFlagQueueTests(TestCase):
    setUp = EntityExtractionLiveStatusTests.setUp
    _create_article = EntityExtractionLiveStatusTests._create_article
    def test_completed_incomplete_extraction_not_in_first_batch(self):
        from apps.assessments.services.extraction_dashboard import select_batch_ids
        article = self._create_article("flag-extracted")
        article.raw_metadata = {"extraction_review_result": {"complete": False}, "extraction_review_state": "completed"}
        article.save(update_fields=["raw_metadata"])
        self.assertNotIn(article.pk, select_batch_ids(50))
        self.assertIn(article.pk, select_batch_ids(50, scope="incomplete"))

    def test_completed_pending_validation_excluded_until_explicit_retry(self):
        from apps.assessments.models import ArticleValidationAssessment
        from apps.assessments.services.process_flags import validation_candidates
        from django.utils import timezone
        article = self._create_article("flag-validated-pending")
        ArticleValidationAssessment.objects.create(article=article, auto_assessed_at=timezone.now())
        self.assertNotIn(article.pk, [a.pk for a, _ in validation_candidates("new")])
        self.assertIn(article.pk, [a.pk for a, _ in validation_candidates("done")])

    def test_claim_prevents_duplicate_and_cross_stage_work(self):
        from apps.assessments.services.process_flags import claim_validation, mark_validation
        from django.utils import timezone
        article = self._create_article("flag-claim")
        self.assertTrue(claim_validation(article.pk, "new"))
        self.assertFalse(claim_validation(article.pk, "new"))
        mark_validation(article.pk, "failed")
        self.assertTrue(claim_validation(article.pk, "failed"))
        mark_validation(article.pk, "failed")
        article.refresh_from_db()
        article.raw_metadata.update(extraction_review_state="running", extraction_review_attempt_at=timezone.now().isoformat())
        article.save(update_fields=["raw_metadata"])
        self.assertFalse(claim_validation(article.pk, "failed"))

    def test_manual_pending_decision_is_not_reassessed(self):
        from apps.assessments.models import ArticleValidationAssessment
        from apps.assessments.services.process_flags import validation_candidates
        article = self._create_article("flag-manual")
        ArticleValidationAssessment.objects.create(article=article, evaluated_by=self.user)
        for mode in ("new", "done", "failed"):
            self.assertNotIn(article.pk, [a.pk for a, _ in validation_candidates(mode)])
