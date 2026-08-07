from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.articles.models import Article
from apps.assessments.models import (
    EarlyWarning,
    EarlyWarningHistory,
    SignalAssessment,
)
from apps.assessments.services import apply_assessment_recommendation
from apps.entities.models import Disease
from apps.locations.models import Location
from apps.signals.models import Signal, SignalArticle
from apps.sources.models import Source


User = get_user_model()


class EarlyWarningWorkspaceTests(TestCase):
    def setUp(self):
        self.analyst = User.objects.create_user(
            username="early-warning-analyst",
            password="test-password-123",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_login(self.analyst)

        self.source = Source.objects.create(
            name="RRI Early Warning",
            code="rri-early-warning",
            domain="rri-warning.example.com",
            base_url="https://rri-warning.example.com",
            source_type=Source.SourceType.GOVERNMENT,
            is_verified=True,
            is_active=True,
        )
        self.article = Article.objects.create(
            source=self.source,
            original_url="https://rri-warning.example.com/tb-tangerang",
            normalized_url="https://rri-warning.example.com/tb-tangerang",
            title="Dinkes catat 5.101 kasus Tuberkulosis",
            content_text=(
                "Dinas Kesehatan mencatat 5.101 kasus Tuberkulosis "
                "di Kabupaten Tangerang."
            ),
            content_hash="e" * 64,
            processing_status=Article.ProcessingStatus.PROCESSED,
        )
        self.disease, _ = Disease.objects.get_or_create(
            name="Tuberkulosis",
            defaults={"code": "tb-early-warning"},
        )
        self.location = Location.objects.create(
            name="Kabupaten Tangerang Early Warning",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            country_code="ID",
        )
        self.signal = Signal.objects.create(
            code="SIG-2026-0001",
            title="Pelaporan 5.101 kasus Tuberkulosis",
            summary=(
                "Pelaporan kasus Tuberkulosis di Kabupaten Tangerang."
            ),
            primary_disease=self.disease,
            primary_location=self.location,
            event_start_date=date(2026, 8, 5),
            status=Signal.Status.VALIDATED,
            validated_by=self.analyst,
            analyst_judgement="Sinyal relevan untuk pemantauan.",
        )
        SignalArticle.objects.create(
            signal=self.signal,
            article=self.article,
            support_type=SignalArticle.SupportType.PRIMARY,
            is_primary_source=True,
        )
        self.assessment = self._create_assessment(
            version=1,
            priority=SignalAssessment.RecommendedPriority.HIGH,
            current=True,
        )
        self.url = reverse("dashboard:early-warning")

    def _create_assessment(self, *, version, priority, current):
        return SignalAssessment.objects.create(
            signal=self.signal,
            version=version,
            is_current=current,
            status=SignalAssessment.Status.COMPLETED,
            urgency_score=4,
            impact_score=4,
            geographic_scope_score=3,
            development_speed_score=4,
            vulnerability_score=3,
            source_reliability_score=0.80,
            information_credibility_score=0.80,
            information_completeness_score=0.60,
            evidence_consistency_score=0.80,
            priority_score=0.76,
            confidence_score=0.75,
            recommended_priority=priority,
            recommended_confidence=(
                SignalAssessment.RecommendedConfidence.HIGH
            ),
            analytical_judgement=(
                "Sinyal memerlukan perhatian dan verifikasi lanjutan."
            ),
            implications="Berpotensi menambah beban layanan kesehatan.",
            recommended_actions=(
                "Verifikasi kepada Dinas Kesehatan dan pantau tren."
            ),
            limitations="Belum tersedia pembanding periode sebelumnya.",
            assessed_by=self.analyst,
            completed_at=timezone.now(),
        )

    def _apply_assessment(self, assessment=None):
        assessment = assessment or self.assessment
        apply_assessment_recommendation(
            assessment=assessment,
            analyst=self.analyst,
            notes="Assessment dikonfirmasi untuk peringatan dini.",
        )

    def _issue_payload(self, assessment=None):
        assessment = assessment or self.assessment
        return {
            "action": "issue_warning",
            "assessment_id": str(assessment.pk),
            "title": "Peringatan Dini Tuberkulosis — Tangerang",
            "summary": (
                "Terdapat pelaporan 5.101 kasus Tuberkulosis di "
                "Kabupaten Tangerang."
            ),
            "recommended_actions": (
                "Verifikasi kepada Dinas Kesehatan dan pantau tren."
            ),
            "decision_notes": (
                "Assessment aktif telah diperiksa dan dikonfirmasi analis."
            ),
        }

    def _issue(self, assessment=None):
        assessment = assessment or self.assessment
        return self.client.post(
            f"{self.url}?assessment={assessment.pk}",
            self._issue_payload(assessment),
        )

    def test_sidebar_opens_early_warning_workspace(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peringatan Dini")
        self.assertContains(response, f'href="{self.url}"', html=False)
        self.assertContains(response, self.signal.code)

    def test_blocks_issue_until_assessment_recommendation_is_applied(self):
        response = self.client.get(
            f"{self.url}?assessment={self.assessment.pk}"
        )

        self.assertContains(response, "Belum siap diterbitkan")
        self.assertContains(response, "belum dikonfirmasi")
        self.assertFalse(EarlyWarning.objects.exists())

    def test_issues_warning_after_analyst_confirmation(self):
        self._apply_assessment()
        response = self._issue()

        self.assertEqual(response.status_code, 302)
        warning = EarlyWarning.objects.get(assessment=self.assessment)
        self.assertTrue(warning.code.startswith("PD-"))
        self.assertTrue(warning.code.endswith("-0001"))
        self.assertEqual(warning.level, EarlyWarning.Level.HIGH)
        self.assertEqual(
            warning.confidence_level,
            SignalAssessment.RecommendedConfidence.HIGH,
        )
        self.assertEqual(warning.status, EarlyWarning.Status.ISSUED)
        self.assertEqual(warning.issued_by, self.analyst)
        self.assertTrue(
            EarlyWarningHistory.objects.filter(
                warning=warning,
                action=EarlyWarningHistory.Action.ISSUED,
            ).exists()
        )

    def test_high_warning_escalates_signal(self):
        self._apply_assessment()
        self._issue()

        self.signal.refresh_from_db()
        self.assertEqual(self.signal.status, Signal.Status.ESCALATED)

    def test_prevents_duplicate_warning_for_same_assessment(self):
        self._apply_assessment()
        self._issue()
        response = self._issue()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(EarlyWarning.objects.count(), 1)
        self.assertContains(response, "sudah tersedia")

    def test_closing_warning_requires_reason(self):
        self._apply_assessment()
        self._issue()
        warning = EarlyWarning.objects.get()

        response = self.client.post(
            self.url,
            {
                "action": "close_warning",
                "assessment_id": str(self.assessment.pk),
                "warning_id": str(warning.pk),
                "closure_notes": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        warning.refresh_from_db()
        self.assertEqual(warning.status, EarlyWarning.Status.ISSUED)

    def test_closes_warning_with_audit_trail(self):
        self._apply_assessment()
        self._issue()
        warning = EarlyWarning.objects.get()

        response = self.client.post(
            self.url,
            {
                "action": "close_warning",
                "assessment_id": str(self.assessment.pk),
                "warning_id": str(warning.pk),
                "closure_notes": (
                    "Verifikasi resmi menunjukkan situasi terkendali."
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        warning.refresh_from_db()
        self.assertEqual(warning.status, EarlyWarning.Status.CLOSED)
        self.assertIsNotNone(warning.closed_at)
        self.assertEqual(warning.closed_by, self.analyst)
        self.assertTrue(
            warning.history.filter(
                action=EarlyWarningHistory.Action.CLOSED
            ).exists()
        )

    def test_new_assessment_supersedes_current_warning(self):
        self._apply_assessment()
        self._issue()
        first_warning = EarlyWarning.objects.get()

        self.assessment.is_current = False
        self.assessment.status = SignalAssessment.Status.SUPERSEDED
        self.assessment.save(update_fields=["is_current", "status"])
        second_assessment = self._create_assessment(
            version=2,
            priority=SignalAssessment.RecommendedPriority.CRITICAL,
            current=True,
        )
        self._apply_assessment(second_assessment)
        response = self._issue(second_assessment)

        self.assertEqual(response.status_code, 302)
        first_warning.refresh_from_db()
        second_warning = EarlyWarning.objects.get(
            assessment=second_assessment
        )
        self.assertFalse(first_warning.is_current)
        self.assertEqual(
            first_warning.status,
            EarlyWarning.Status.SUPERSEDED,
        )
        self.assertTrue(second_warning.is_current)
        self.assertEqual(second_warning.version, 2)
        self.assertEqual(second_warning.level, EarlyWarning.Level.CRITICAL)
