"""Regresi 02B8N: alasan tersimpan + periode inklusif + audit trail."""
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.permissions import Roles
from apps.articles.models import Article
from apps.assessments.forms import PrimaryArticleDiseaseForm, PrimaryArticleLocationForm
from apps.assessments.models import ArticleValidationAssessment
from apps.assessments.services.extraction_dashboard import evidence_queryset, queue_rows
from apps.entities.models import (
    ArticleDisease, ArticleFact, ArticleLocation, Disease,
    ExtractionReviewLog, ValidationStatus,
)
from apps.locations.models import Location
from apps.signals.services.formation import evaluate_article_signal_candidate
from apps.sources.models import Source


User = get_user_model()


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class ReportingPeriodReviewTests(TestCase):
    def setUp(self):
        self.reviewer = User.objects.create_user(
            username="reviewer-02b8n", password="reviewer-test!",
            is_superuser=True, is_staff=True,
        )
        self.source = Source.objects.create(
            name="Sumber 02B8N", code="sumber-02b8n",
            domain="source-02b8n.example.org", base_url="https://source-02b8n.example.org",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True, is_active=True,
        )
        self.article = Article.objects.create(
            source=self.source,
            original_url="https://source-02b8n.example.org/pekanbaru",
            normalized_url="https://source-02b8n.example.org/pekanbaru",
            title="534 kasus DBD Pekanbaru hingga Juli 2026",
            content_text="Januari hingga Juli 2026 terdapat 534 kasus DBD dengan satu kematian di Pekanbaru.",
            content_hash="b" * 64,
            raw_metadata={"information_type_review": {
                "category": "case_event", "source": "manual_analyst"
            }},
        )
        self.disease = Disease.objects.create(
            name="DBD Rentang 02B8N", code="dbd-rentang-02b8n",
        )
        self.location = Location.objects.create(
            name="Kota Uji 02B8N", code="02b8n-kota",
            administrative_level=Location.AdministrativeLevel.CITY,
            country_code="ID",
        )
        self.disease_relation = ArticleDisease.objects.create(
            article=self.article, disease=self.disease, is_primary=True,
            validation_status=ValidationStatus.CORRECTED,
            validation_notes="DBD sebagai penyakit utama pada sumber",
        )
        self.location_relation = ArticleLocation.objects.create(
            article=self.article, location=self.location, is_primary=True,
            validation_status=ValidationStatus.VALIDATED,
            validation_notes="Kota pada sumber",
        )
        self.fact = ArticleFact.objects.create(
            article=self.article, disease=self.disease, location=self.location,
            case_count=534, death_count=None, event_date=None,
            fact_text="Sebanyak 534 kasus DBD di Pekanbaru Januari hingga Juli 2026.",
            validation_status=ValidationStatus.VALIDATED,
        )
        ArticleValidationAssessment.objects.create(
            article=self.article,
            validation_status=ArticleValidationAssessment.ValidationStatus.VALIDATED,
            source_reliability=ArticleValidationAssessment.SourceReliability.B,
            information_credibility=ArticleValidationAssessment.InformationCredibility.PROBABLY_TRUE,
            evaluated_by=self.reviewer,
        )

    def url(self):
        return reverse("dashboard:fact-period-review", kwargs={
            "article_id": self.article.pk, "fact_id": self.fact.pk,
        })

    def payload(self, **overrides):
        fields = {
            "reporting_period_start": "2026-01-01",
            "reporting_period_end": "2026-07-31",
            "reporting_period_precision": "month",
            "case_count_basis": "cumulative",
            "death_count": "1",
            "fact_text": "Periode Januari hingga Juli 2026 terdapat 534 kasus DBD dan 1 kematian.",
            "correction_reason": "Kutipan sumber periode Januari-Juli serta satu kematian.",
            "version": self.fact.updated_at.isoformat(),
        }
        fields.update(overrides)
        return fields

    def test_reasoning_is_prefilled_without_overwriting_bound_data(self):
        disease_form = PrimaryArticleDiseaseForm(article=self.article)
        location_form = PrimaryArticleLocationForm(article=self.article)
        self.assertEqual(disease_form.initial["disease_correction_notes"],
                         "DBD sebagai penyakit utama pada sumber")
        self.assertEqual(location_form.initial["correction_notes"], "Kota pada sumber")
        bound = PrimaryArticleDiseaseForm(
            {"primary_disease": self.disease.pk, "disease_correction_notes": "Alasan baru"},
            article=self.article,
        )
        self.assertEqual(bound["disease_correction_notes"].value(), "Alasan baru")

    def test_valid_period_correction_keeps_event_date_empty_and_logs_change(self):
        self.client.force_login(self.reviewer)
        response = self.client.post(self.url(), self.payload())
        self.assertEqual(response.status_code, 302)
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.reporting_period_start, date(2026, 1, 1))
        self.assertEqual(self.fact.reporting_period_end, date(2026, 7, 31))
        self.assertEqual(self.fact.case_count_basis, ArticleFact.CaseCountBasis.CUMULATIVE)
        self.assertEqual(self.fact.reporting_period_precision, ArticleFact.ReportingPrecision.MONTH)
        self.assertEqual(self.fact.death_count, 1)
        self.assertIsNone(self.fact.event_date)
        self.assertEqual(self.fact.validation_status, ValidationStatus.CORRECTED)
        log = ExtractionReviewLog.objects.get(
            object_type=ExtractionReviewLog.ObjectType.ARTICLE_FACT, object_id=self.fact.id,
            action=ExtractionReviewLog.Action.CORRECT,
        )
        self.assertIsNone(log.before_data["reporting_period_start"])
        self.assertEqual(log.after_data["reporting_period_start"], "2026-01-01")
        self.assertIn("sumber", log.notes.lower())

    def test_date_window_validation_never_sets_a_fake_event_date(self):
        self.client.force_login(self.reviewer)
        response = self.client.post(self.url(), self.payload(reporting_period_end="2025-07-31"))
        self.assertEqual(response.status_code, 200)
        self.fact.refresh_from_db()
        self.assertIsNone(self.fact.reporting_period_start)
        self.assertIsNone(self.fact.event_date)
        self.assertFalse(ExtractionReviewLog.objects.filter(object_id=self.fact.pk).exists())

    def test_viewer_cannot_post_correction(self):
        viewer = User.objects.create_user(username="viewer-02b8n", password="test!")
        group, _ = Group.objects.get_or_create(name=Roles.VIEWER)
        viewer.groups.add(group)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(self.client.post(self.url(), self.payload()).status_code, 403)
        self.fact.refresh_from_db()
        self.assertIsNone(self.fact.reporting_period_start)

    def test_core_review_gate_accepts_period_but_signal_daily_gate_blocks_it(self):
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.post(self.url(), self.payload()).status_code, 302)
        article = evidence_queryset().get(pk=self.article.pk)
        self.assertTrue(article.extraction_has_reviewed_case_fact)
        row = next(row for row in queue_rows() if row["article"].pk == self.article.pk)
        self.assertTrue(row["analysis_ready"])
        candidate = evaluate_article_signal_candidate(self.article)
        self.assertFalse(candidate.is_ready)
        self.assertTrue(any("rentang" in text.lower() for text in candidate.blockers))

    def test_stale_form_is_rejected_and_previous_revision_preserved(self):
        self.client.force_login(self.reviewer)
        submitted = self.payload(version="2020-01-01T00:00:00+00:00")
        response = self.client.post(self.url(), submitted)
        self.assertEqual(response.status_code, 200)
        self.fact.refresh_from_db()
        self.assertIsNone(self.fact.reporting_period_start)
        self.assertFalse(ExtractionReviewLog.objects.filter(object_id=self.fact.pk).exists())

    def test_saved_values_and_reason_remain_visible_after_reload(self):
        self.client.force_login(self.reviewer)
        response = self.client.post(self.url(), self.payload(), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Koreksi berhasil disimpan")
        self.assertContains(response, 'id="fact-saved-summary"')
        self.assertContains(response, "Kutipan sumber periode Januari-Juli")
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.death_count, 1)
        self.assertEqual(self.fact.reporting_period_end, date(2026, 7, 31))

    def test_invalid_period_shows_prominent_error_and_keeps_input(self):
        self.client.force_login(self.reviewer)
        response = self.client.post(
            self.url(), self.payload(reporting_period_end="2025-07-31")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Belum tersimpan")
        self.assertContains(response, 'id="fact-save-feedback"')
        self.assertContains(response, 'value="2025-07-31"')
        self.fact.refresh_from_db()
        self.assertIsNone(self.fact.reporting_period_start)

    def test_reason_only_change_is_audited_and_prefilled(self):
        self.client.force_login(self.reviewer)
        fields = self.payload(
            reporting_period_start="",
            reporting_period_end="",
            reporting_period_precision="unspecified",
            case_count_basis="unknown",
            death_count="",
            fact_text=self.fact.fact_text,
            correction_reason="Alasan baru berdasarkan evaluasi bukti sumber.",
        )
        response = self.client.post(self.url(), fields)
        self.assertEqual(response.status_code, 302)
        self.fact.refresh_from_db()
        self.assertIsNone(self.fact.reporting_period_start)
        self.assertEqual(
            self.fact.validation_notes,
            "Alasan baru berdasarkan evaluasi bukti sumber.",
        )
        self.assertEqual(self.fact.validation_status, ValidationStatus.CORRECTED)
        self.assertTrue(ExtractionReviewLog.objects.filter(
            object_type=ExtractionReviewLog.ObjectType.ARTICLE_FACT,
            object_id=self.fact.pk,
            action=ExtractionReviewLog.Action.CORRECT,
        ).exists())
        response = self.client.get(self.url())
        self.assertContains(response, "Alasan baru berdasarkan evaluasi bukti sumber.")

    def test_stale_version_explains_not_saved_and_preserves_draft(self):
        self.client.force_login(self.reviewer)
        response = self.client.post(
            self.url(), self.payload(version="2020-01-01T00:00:00+00:00")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Belum tersimpan")
        self.assertContains(response, "telah diperbarui sejak halaman dibuka")
        self.assertContains(response, 'value="2026-01-01"')
        self.fact.refresh_from_db()
        self.assertIsNone(self.fact.reporting_period_start)

    def test_model_rejects_single_event_and_period_combination(self):
        self.fact.event_date = date(2026, 7, 31)
        self.fact.reporting_period_start = date(2026, 1, 1)
        self.fact.reporting_period_end = date(2026, 7, 31)
        self.fact.reporting_period_precision = ArticleFact.ReportingPrecision.MONTH
        with self.assertRaises(ValidationError):
            self.fact.full_clean()
