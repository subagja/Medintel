from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.assessments.models import EarlyWarning, SignalAssessment
from apps.entities.models import Disease
from apps.locations.models import Location
from apps.signals.models import Signal


User = get_user_model()


class DashboardThreatLevelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="dashboard-threat-admin",
            password="test-password-123",
            email="dashboard@example.com",
        )
        self.client.force_login(self.user)
        self.disease = Disease.objects.create(
            name="Penyakit Dashboard Threat Test",
            code="dashboard-threat-test",
        )
        self.location = Location.objects.create(
            name="Lokasi Dashboard Threat Test",
            administrative_level=Location.AdministrativeLevel.CITY,
            country_code="ID",
        )

    def _signal(self, code: str, *, status=Signal.Status.VALIDATED):
        return Signal.objects.create(
            code=code,
            title=f"Sinyal {code}",
            summary="Ringkasan sinyal untuk pengujian dashboard.",
            primary_disease=self.disease,
            primary_location=self.location,
            status=status,
        )

    def _assessment(self, signal, priority):
        score = {
            SignalAssessment.RecommendedPriority.LOW: 0.25,
            SignalAssessment.RecommendedPriority.MEDIUM: 0.50,
            SignalAssessment.RecommendedPriority.HIGH: 0.75,
            SignalAssessment.RecommendedPriority.CRITICAL: 0.95,
        }[priority]
        return SignalAssessment.objects.create(
            signal=signal,
            version=1,
            is_current=True,
            status=SignalAssessment.Status.COMPLETED,
            urgency_score=3,
            impact_score=3,
            geographic_scope_score=3,
            development_speed_score=3,
            vulnerability_score=3,
            source_reliability_score=0.8,
            information_credibility_score=0.8,
            information_completeness_score=0.8,
            evidence_consistency_score=0.8,
            priority_score=score,
            confidence_score=0.8,
            recommended_priority=priority,
            recommended_confidence=(
                SignalAssessment.RecommendedConfidence.HIGH
            ),
            analytical_judgement="Judgement untuk pengujian dashboard.",
            assessed_by=self.user,
        )

    def test_unassessed_escalated_signals_are_not_treated_as_risk_score(self):
        for index in range(5):
            self._signal(
                f"SIG-UNASSESSED-{index}",
                status=Signal.Status.ESCALATED,
            )

        response = self.client.get(reverse("dashboard:overview"))

        self.assertEqual(response.context["threat_level"], "Belum Dinilai")
        self.assertContains(response, "Belum ada assessment")

    def test_single_critical_assessment_sets_critical_threat_level(self):
        signal = self._signal("SIG-CRITICAL-0001")
        self._assessment(
            signal,
            SignalAssessment.RecommendedPriority.CRITICAL,
        )

        response = self.client.get(reverse("dashboard:overview"))

        self.assertEqual(response.context["threat_level"], "Kritis")
        self.assertEqual(
            response.context["threat_level_basis"],
            "Assessment aktif SIG-CRITICAL-0001",
        )

    def test_highest_active_warning_wins_and_stale_warning_is_not_counted(self):
        low_signal = self._signal("SIG-LOW-0001")
        low_assessment = self._assessment(
            low_signal,
            SignalAssessment.RecommendedPriority.LOW,
        )
        EarlyWarning.objects.create(
            code="PD-2026-0001",
            signal=low_signal,
            assessment=low_assessment,
            version=1,
            is_current=False,
            level=EarlyWarning.Level.CRITICAL,
            confidence_level=SignalAssessment.RecommendedConfidence.HIGH,
            status=EarlyWarning.Status.CLOSED,
            title="Peringatan lama",
            summary="Peringatan yang sudah tidak aktif.",
            analytical_judgement="Judgement lama.",
            recommended_actions="Tidak ada tindakan aktif.",
            decision_notes="Ditutup.",
        )

        high_signal = self._signal("SIG-HIGH-0001")
        high_assessment = self._assessment(
            high_signal,
            SignalAssessment.RecommendedPriority.HIGH,
        )
        EarlyWarning.objects.create(
            code="PD-2026-0002",
            signal=high_signal,
            assessment=high_assessment,
            version=1,
            is_current=True,
            level=EarlyWarning.Level.HIGH,
            confidence_level=SignalAssessment.RecommendedConfidence.HIGH,
            status=EarlyWarning.Status.ISSUED,
            title="Peringatan aktif",
            summary="Peringatan tinggi yang masih aktif.",
            analytical_judgement="Judgement aktif.",
            recommended_actions="Lakukan verifikasi segera.",
            decision_notes="Diterbitkan analis.",
        )

        response = self.client.get(reverse("dashboard:overview"))

        self.assertEqual(response.context["threat_level"], "Tinggi")
        self.assertEqual(response.context["active_warning_count"], 1)
        self.assertEqual(
            response.context["threat_level_basis"],
            "Peringatan dini aktif PD-2026-0002",
        )
