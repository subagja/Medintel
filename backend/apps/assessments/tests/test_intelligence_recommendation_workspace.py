from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.assessments.models import (
    EarlyWarning,
    IntelligenceRecommendation,
    IntelligenceRecommendationHistory,
    SignalAssessment,
)
from apps.assessments.services import apply_assessment_recommendation
from apps.entities.models import Disease, Location
from apps.signals.models import Signal


User = get_user_model()


class IntelligenceRecommendationWorkspaceTests(TestCase):
    def setUp(self):
        self.analyst = User.objects.create_user(
            username="intelligence-recommendation-analyst",
            password="test-password-123",
        )
        self.client.force_login(self.analyst)
        self.disease = Disease.objects.create(
            name="Tuberkulosis Recommendation Test",
            code="tb-intelligence-recommendation-test",
            is_active=True,
        )
        self.location = Location.objects.create(
            name="Kabupaten Tangerang Recommendation Test",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            country_code="ID",
        )
        self.signal = Signal.objects.create(
            code="SIG-REC-0001",
            title="Pelaporan kasus Tuberkulosis di Tangerang",
            summary=(
                "Pelaporan kasus Tuberkulosis memerlukan verifikasi."
            ),
            primary_disease=self.disease,
            primary_location=self.location,
            event_start_date=date(2026, 8, 5),
            status=Signal.Status.VALIDATED,
            priority_level=Signal.PriorityLevel.MEDIUM,
            confidence_level=Signal.ConfidenceLevel.MEDIUM,
            analyst_judgement="Sinyal relevan untuk pemantauan.",
            recommended_action="Verifikasi kepada Dinas Kesehatan.",
        )
        self.assessment = self._assessment(
            version=1,
            priority=SignalAssessment.RecommendedPriority.MEDIUM,
            current=True,
        )
        self.url = reverse("dashboard:intelligence-recommendation")

    def _assessment(self, *, version, priority, current):
        score = {
            SignalAssessment.RecommendedPriority.LOW: 0.30,
            SignalAssessment.RecommendedPriority.MEDIUM: 0.60,
            SignalAssessment.RecommendedPriority.HIGH: 0.80,
            SignalAssessment.RecommendedPriority.CRITICAL: 0.95,
        }[priority]
        return SignalAssessment.objects.create(
            signal=self.signal,
            version=version,
            is_current=current,
            status=SignalAssessment.Status.COMPLETED,
            urgency_score=3,
            impact_score=3,
            geographic_scope_score=3,
            development_speed_score=3,
            vulnerability_score=3,
            source_reliability_score=0.80,
            information_credibility_score=0.80,
            information_completeness_score=0.60,
            evidence_consistency_score=0.80,
            priority_score=score,
            confidence_score=0.72,
            recommended_priority=priority,
            recommended_confidence=(
                SignalAssessment.RecommendedConfidence.MEDIUM
            ),
            analytical_judgement=(
                "Kasus memerlukan verifikasi dan pemantauan lanjutan."
            ),
            implications="Berpotensi menambah beban layanan kesehatan.",
            recommended_actions=(
                "Konfirmasi data kepada Dinas Kesehatan dan cari sumber "
                "pembanding."
            ),
            assumptions="Angka pada artikel merujuk periode berjalan.",
            limitations="Belum tersedia pembanding periode sebelumnya.",
            assessed_by=self.analyst,
            completed_at=timezone.now(),
        )

    def _apply(self, assessment=None):
        assessment = assessment or self.assessment
        apply_assessment_recommendation(
            assessment=assessment,
            analyst=self.analyst,
            notes="Assessment dikonfirmasi untuk rekomendasi intelijen.",
        )

    def _payload(self, assessment=None, **overrides):
        assessment = assessment or self.assessment
        payload = {
            "action": "create_draft",
            "assessment_id": str(assessment.pk),
            "title": (
                "Rekomendasi Intelijen Tuberkulosis — Tangerang"
            ),
            "situation_summary": (
                "Kasus memerlukan verifikasi dan pemantauan lanjutan."
            ),
            "objective": (
                "Memastikan angka kasus dan periode pelaporan tervalidasi."
            ),
            "recommended_action": (
                "Konfirmasi kepada Dinas Kesehatan dan cari sumber "
                "pembanding."
            ),
            "action_category": (
                IntelligenceRecommendation.ActionCategory.VERIFICATION
            ),
            "urgency": IntelligenceRecommendation.Urgency.PRIORITY,
            "target_unit": "Dinas Kesehatan Kabupaten Tangerang",
            "due_date": (timezone.localdate() + timedelta(days=3)).isoformat(),
            "success_indicators": (
                "Tersedia konfirmasi resmi dan data pembanding."
            ),
            "assumptions": "Periode pelaporan masih berjalan.",
            "information_gaps": "Belum ada data pembanding.",
        }
        payload.update(overrides)
        return payload

    def _create(self, assessment=None, **overrides):
        assessment = assessment or self.assessment
        return self.client.post(
            f"{self.url}?assessment={assessment.pk}",
            self._payload(assessment, **overrides),
        )

    def _recommendation(self, assessment=None):
        assessment = assessment or self.assessment
        return IntelligenceRecommendation.objects.get(
            assessment=assessment
        )

    def test_sidebar_opens_intelligence_recommendation_workspace(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rekomendasi Intelijen")
        self.assertContains(response, f'href="{self.url}"', html=False)
        self.assertContains(response, self.signal.code)
        self.assertContains(response, "bukan keputusan otomatis")

    def test_unconfirmed_assessment_cannot_create_recommendation(self):
        response = self._create()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(IntelligenceRecommendation.objects.exists())
        self.assertContains(response, "belum dikonfirmasi")

    def test_confirmed_assessment_creates_draft_not_operational_decision(self):
        self._apply()
        response = self._create()

        self.assertEqual(response.status_code, 302)
        recommendation = self._recommendation()
        self.assertTrue(recommendation.code.startswith("RI-"))
        self.assertTrue(recommendation.code.endswith("-0001"))
        self.assertEqual(
            recommendation.status,
            IntelligenceRecommendation.Status.DRAFT,
        )
        self.assertIsNone(recommendation.approved_at)
        self.assertEqual(recommendation.created_by, self.analyst)
        self.assertTrue(
            recommendation.history.filter(
                action=(
                    IntelligenceRecommendationHistory.Action.DRAFTED
                )
            ).exists()
        )

    def test_default_draft_uses_assessment_without_hiding_analyst_gate(self):
        self._apply()
        response = self.client.get(
            self.url,
            {"assessment": str(self.assessment.pk)},
        )

        self.assertContains(response, self.assessment.recommended_actions)
        self.assertContains(
            response,
            'option value="priority" selected',
            html=False,
        )
        self.assertContains(response, "Draf belum menjadi arahan operasional")

    def test_past_due_date_is_rejected(self):
        self._apply()
        response = self._create(
            due_date=(timezone.localdate() - timedelta(days=1)).isoformat()
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(IntelligenceRecommendation.objects.exists())
        self.assertContains(response, "tidak boleh berada di masa lalu")

    def test_active_early_warning_is_linked_as_lineage(self):
        self._apply()
        warning = EarlyWarning.objects.create(
            code="PD-REC-0001",
            signal=self.signal,
            assessment=self.assessment,
            version=1,
            is_current=True,
            level=EarlyWarning.Level.ADVISORY,
            confidence_level=self.assessment.recommended_confidence,
            status=EarlyWarning.Status.ISSUED,
            title="Peringatan Dini Tuberkulosis",
            summary=self.signal.summary,
            analytical_judgement=self.assessment.analytical_judgement,
            implications=self.assessment.implications,
            recommended_actions=self.assessment.recommended_actions,
            information_gaps=self.assessment.limitations,
            decision_notes="Diterbitkan setelah konfirmasi analis.",
            issued_by=self.analyst,
        )

        self._create()
        recommendation = self._recommendation()
        response = self.client.get(
            self.url,
            {"assessment": str(self.assessment.pk)},
        )

        self.assertEqual(recommendation.early_warning, warning)
        self.assertContains(response, warning.code)
        self.assertContains(response, reverse("dashboard:early-warning"))

    def test_approval_requires_analyst_rationale(self):
        self._apply()
        self._create()
        recommendation = self._recommendation()

        response = self.client.post(
            self.url,
            {
                "action": "approve",
                "assessment_id": str(self.assessment.pk),
                "recommendation_id": str(recommendation.pk),
                "decision_notes": "",
            },
        )

        recommendation.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            recommendation.status,
            IntelligenceRecommendation.Status.DRAFT,
        )

    def test_analyst_can_approve_and_start_with_audit_trail(self):
        self._apply()
        self._create()
        recommendation = self._recommendation()

        approve_response = self.client.post(
            self.url,
            {
                "action": "approve",
                "assessment_id": str(self.assessment.pk),
                "recommendation_id": str(recommendation.pk),
                "decision_notes": (
                    "Rekomendasi relevan, terukur, dan sesuai assessment."
                ),
            },
        )
        start_response = self.client.post(
            self.url,
            {
                "action": "start",
                "assessment_id": str(self.assessment.pk),
                "recommendation_id": str(recommendation.pk),
                "progress_notes": "Permintaan konfirmasi telah dikirim.",
            },
        )

        recommendation.refresh_from_db()
        self.assertEqual(approve_response.status_code, 302)
        self.assertEqual(start_response.status_code, 302)
        self.assertEqual(
            recommendation.status,
            IntelligenceRecommendation.Status.IN_PROGRESS,
        )
        self.assertEqual(recommendation.approved_by, self.analyst)
        self.assertTrue(
            recommendation.history.filter(
                action=IntelligenceRecommendationHistory.Action.APPROVED
            ).exists()
        )
        self.assertTrue(
            recommendation.history.filter(
                action=IntelligenceRecommendationHistory.Action.STARTED
            ).exists()
        )

    def test_completion_records_result_and_closes_current_item(self):
        self._apply()
        self._create()
        recommendation = self._recommendation()
        recommendation.status = IntelligenceRecommendation.Status.APPROVED
        recommendation.approved_by = self.analyst
        recommendation.approved_at = timezone.now()
        recommendation.save(
            update_fields=["status", "approved_by", "approved_at"]
        )

        response = self.client.post(
            self.url,
            {
                "action": "complete",
                "assessment_id": str(self.assessment.pk),
                "recommendation_id": str(recommendation.pk),
                "completion_notes": (
                    "Dinas Kesehatan telah mengonfirmasi angka kasus."
                ),
            },
        )

        recommendation.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            recommendation.status,
            IntelligenceRecommendation.Status.COMPLETED,
        )
        self.assertFalse(recommendation.is_current)
        self.assertEqual(recommendation.completed_by, self.analyst)
        self.assertIsNotNone(recommendation.completed_at)

    def test_cancel_requires_reason_and_keeps_audit_trail(self):
        self._apply()
        self._create()
        recommendation = self._recommendation()

        invalid_response = self.client.post(
            self.url,
            {
                "action": "cancel",
                "assessment_id": str(self.assessment.pk),
                "recommendation_id": str(recommendation.pk),
                "cancellation_reason": "",
            },
        )
        recommendation.refresh_from_db()
        self.assertEqual(invalid_response.status_code, 200)
        self.assertEqual(
            recommendation.status,
            IntelligenceRecommendation.Status.DRAFT,
        )

        valid_response = self.client.post(
            self.url,
            {
                "action": "cancel",
                "assessment_id": str(self.assessment.pk),
                "recommendation_id": str(recommendation.pk),
                "cancellation_reason": (
                    "Assessment perlu diperbarui dengan data resmi baru."
                ),
            },
        )

        recommendation.refresh_from_db()
        self.assertEqual(valid_response.status_code, 302)
        self.assertEqual(
            recommendation.status,
            IntelligenceRecommendation.Status.CANCELED,
        )
        self.assertFalse(recommendation.is_current)
        self.assertTrue(
            recommendation.history.filter(
                action=IntelligenceRecommendationHistory.Action.CANCELED
            ).exists()
        )

    def test_duplicate_recommendation_for_same_assessment_is_prevented(self):
        self._apply()
        self._create()
        response = self._create()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(IntelligenceRecommendation.objects.count(), 1)
        self.assertContains(response, "sudah tersedia")

    def test_new_assessment_supersedes_current_recommendation(self):
        self._apply()
        self._create()
        first_recommendation = self._recommendation()

        self.assessment.is_current = False
        self.assessment.status = SignalAssessment.Status.SUPERSEDED
        self.assessment.save(update_fields=["is_current", "status"])
        second_assessment = self._assessment(
            version=2,
            priority=SignalAssessment.RecommendedPriority.HIGH,
            current=True,
        )
        self._apply(second_assessment)

        response = self._create(
            second_assessment,
            urgency=IntelligenceRecommendation.Urgency.URGENT,
            action_category=(
                IntelligenceRecommendation.ActionCategory.COORDINATION
            ),
        )

        first_recommendation.refresh_from_db()
        second_recommendation = self._recommendation(second_assessment)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(first_recommendation.is_current)
        self.assertEqual(
            first_recommendation.status,
            IntelligenceRecommendation.Status.SUPERSEDED,
        )
        self.assertEqual(second_recommendation.version, 2)
        self.assertTrue(second_recommendation.is_current)
        self.assertTrue(
            first_recommendation.history.filter(
                action=(
                    IntelligenceRecommendationHistory.Action.SUPERSEDED
                )
            ).exists()
        )

        archive_response = self.client.get(
            self.url,
            {"recommendation": str(first_recommendation.pk)},
        )
        self.assertEqual(archive_response.status_code, 200)
        self.assertContains(archive_response, first_recommendation.code)
        self.assertContains(archive_response, "Digantikan")
