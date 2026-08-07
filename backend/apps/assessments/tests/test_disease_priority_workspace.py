from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.assessments.models import EarlyWarning, SignalAssessment
from apps.assessments.services.disease_priority import (
    build_disease_priority_dataset,
)
from apps.entities.models import (
    Disease,
    SurveillanceDisease,
    SurveillanceProgram,
)
from apps.locations.models import Location
from apps.signals.models import Signal, SignalHistory


User = get_user_model()


class DiseasePriorityWorkspaceTests(TestCase):
    def setUp(self):
        self.analyst = User.objects.create_user(
            username="disease-priority-analyst",
            password="test-password-123",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_login(self.analyst)
        self.program, _ = SurveillanceProgram.objects.get_or_create(
            code="skdr-penyakit-menular",
            defaults={
                "name": "Program Surveilans Penyakit Menular",
                "status": SurveillanceProgram.Status.ACTIVE,
            },
        )
        if self.program.status != SurveillanceProgram.Status.ACTIVE:
            self.program.status = SurveillanceProgram.Status.ACTIVE
            self.program.save(update_fields=["status", "updated_at"])
        self.location = Location.objects.create(
            name="Kabupaten Tangerang Priority Test",
            code="3699",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            country_code="ID",
        )
        self.tb = self._disease(
            "Tuberkulosis Priority Test",
            "tb-priority-test",
            "menular_langsung",
        )
        self.dbd = self._disease(
            "Demam Berdarah Dengue Priority Test",
            "dbd-priority-test",
            "potensial_klb",
        )
        self.malaria = self._disease(
            "Malaria Priority Test",
            "malaria-priority-test",
            "tular_vektor",
        )
        self.tb_signal = self._signal(
            code="SIG-PRI-0001",
            disease=self.tb,
            status=Signal.Status.VALIDATED,
        )
        self.tb_assessment = self._assessment(
            self.tb_signal,
            priority=SignalAssessment.RecommendedPriority.MEDIUM,
        )
        self._apply(self.tb_assessment)
        self.tb_warning = self._warning(
            self.tb_signal,
            self.tb_assessment,
            code="PD-PRI-0001",
            level=EarlyWarning.Level.ADVISORY,
        )
        self.url = reverse("dashboard:disease-priority")

    def _disease(self, name, code, category):
        disease = Disease.objects.create(
            name=name,
            canonical_name=name,
            code=code,
            category=category,
            is_priority=True,
            is_active=True,
        )
        SurveillanceDisease.objects.create(
            program=self.program,
            disease=disease,
            official_name=name,
            category=category,
            is_active=True,
        )
        return disease

    def _signal(self, *, code, disease, status):
        return Signal.objects.create(
            code=code,
            title=f"Sinyal {disease.name}",
            summary=f"Pemantauan {disease.name} di Tangerang.",
            primary_disease=disease,
            primary_location=self.location,
            status=status,
            priority_level=Signal.PriorityLevel.MEDIUM,
            confidence_level=Signal.ConfidenceLevel.MEDIUM,
            analyst_judgement="Perlu pemantauan dan verifikasi.",
        )

    def _assessment(self, signal, *, priority):
        score = {
            SignalAssessment.RecommendedPriority.LOW: 0.30,
            SignalAssessment.RecommendedPriority.MEDIUM: 0.60,
            SignalAssessment.RecommendedPriority.HIGH: 0.80,
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
            analytical_judgement="Judgement analis untuk pengujian.",
            implications="Implikasi untuk pengujian.",
            recommended_actions="Lakukan verifikasi lanjutan.",
            limitations="Masih memerlukan sumber pembanding.",
            assessed_by=self.analyst,
            completed_at=timezone.now(),
        )

    def _apply(self, assessment):
        SignalHistory.objects.create(
            signal=assessment.signal,
            from_status=assessment.signal.status,
            to_status=assessment.signal.status,
            changed_by=self.analyst,
            reason="Assessment dikonfirmasi analis.",
            metadata={
                "action": "assessment_applied",
                "assessment_id": str(assessment.pk),
            },
        )

    def _warning(self, signal, assessment, *, code, level):
        return EarlyWarning.objects.create(
            code=code,
            signal=signal,
            assessment=assessment,
            version=assessment.version,
            is_current=True,
            level=level,
            confidence_level=assessment.recommended_confidence,
            status=EarlyWarning.Status.ISSUED,
            title=f"Peringatan Dini {signal.primary_disease.name}",
            summary=signal.summary,
            analytical_judgement=assessment.analytical_judgement,
            implications=assessment.implications,
            recommended_actions=assessment.recommended_actions,
            information_gaps=assessment.limitations,
            decision_notes="Diterbitkan setelah konfirmasi analis.",
            issued_by=self.analyst,
        )

    def _item(self, disease):
        return next(
            item
            for item in build_disease_priority_dataset()
            if item["id"] == str(disease.pk)
        )

    def test_sidebar_opens_priority_workspace(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Penyakit Prioritas")
        self.assertContains(response, f'href="{self.url}"', html=False)
        self.assertContains(response, self.tb.name)
        self.assertContains(response, self.dbd.name)
        self.assertContains(response, self.malaria.name)

    def test_active_warning_defines_operational_attention(self):
        item = self._item(self.tb)

        self.assertEqual(item["attention"]["key"], "advisory")
        self.assertEqual(item["attention"]["label"], "Waspada")
        self.assertEqual(item["active_warning_count"], 1)
        self.assertIn(self.tb_warning.code, item["attention"]["basis"])

    def test_unconfirmed_assessment_cannot_raise_attention(self):
        signal = self._signal(
            code="SIG-PRI-0002",
            disease=self.dbd,
            status=Signal.Status.VALIDATED,
        )
        self._assessment(
            signal,
            priority=SignalAssessment.RecommendedPriority.HIGH,
        )

        item = self._item(self.dbd)
        response = self.client.get(
            self.url,
            {"disease": str(self.dbd.pk)},
        )

        self.assertEqual(item["attention"]["key"], "unassessed")
        self.assertEqual(item["attention"]["label"], "Belum Dinilai")
        self.assertContains(response, "Belum dikonfirmasi")

    def test_confirmed_assessment_sets_attention_without_warning(self):
        signal = self._signal(
            code="SIG-PRI-0003",
            disease=self.dbd,
            status=Signal.Status.VALIDATED,
        )
        assessment = self._assessment(
            signal,
            priority=SignalAssessment.RecommendedPriority.HIGH,
        )
        self._apply(assessment)

        item = self._item(self.dbd)

        self.assertEqual(item["attention"]["key"], "high")
        self.assertIn("dikonfirmasi", item["attention"]["basis"])
        self.assertEqual(item["active_warning_count"], 0)

    def test_closed_warning_is_not_used_as_active_basis(self):
        self.tb_warning.status = EarlyWarning.Status.CLOSED
        self.tb_warning.is_current = False
        self.tb_warning.save(update_fields=["status", "is_current"])
        SignalHistory.objects.filter(
            signal=self.tb_signal,
            metadata__action="assessment_applied",
        ).delete()

        item = self._item(self.tb)

        self.assertEqual(item["attention"]["key"], "unassessed")
        self.assertEqual(item["active_warning_count"], 0)

    def test_trend_compares_validated_signals_across_two_windows(self):
        older = self._signal(
            code="SIG-PRI-0004",
            disease=self.tb,
            status=Signal.Status.CLOSED,
        )
        Signal.objects.filter(pk=older.pk).update(
            first_detected_at=timezone.now() - timedelta(days=10)
        )

        item = self._item(self.tb)

        self.assertEqual(item["current_signal_count"], 1)
        self.assertEqual(item["previous_signal_count"], 1)
        self.assertEqual(item["trend"]["key"], "steady")

    def test_category_attention_and_search_filters_are_applied(self):
        category_response = self.client.get(
            self.url,
            {"category": "menular_langsung"},
        )
        attention_response = self.client.get(
            self.url,
            {"attention": "advisory"},
        )
        search_response = self.client.get(
            self.url,
            {"q": "Malaria"},
        )

        self.assertContains(category_response, self.tb.name)
        self.assertNotContains(category_response, self.dbd.name)
        self.assertContains(attention_response, self.tb.name)
        self.assertNotContains(attention_response, self.malaria.name)
        self.assertContains(search_response, self.malaria.name)
        self.assertNotContains(search_response, self.tb.name)

    def test_selected_detail_links_to_existing_intelligence_products(self):
        response = self.client.get(
            self.url,
            {"disease": str(self.tb.pk)},
        )

        self.assertContains(response, self.tb_signal.code)
        self.assertContains(
            response,
            reverse("dashboard:signal-workspace"),
        )
        self.assertContains(
            response,
            reverse("dashboard:threat-assessment"),
        )
        self.assertContains(
            response,
            reverse("dashboard:early-warning"),
        )
        self.assertContains(response, reverse("dashboard:threat-map"))
        self.assertContains(response, "Dikonfirmasi analis")

    def test_priority_master_remains_visible_without_signal(self):
        item = self._item(self.malaria)

        self.assertEqual(item["attention"]["key"], "none")
        self.assertEqual(item["active_signal_count"], 0)
        self.assertEqual(item["active_warning_count"], 0)

    def test_active_surveillance_membership_includes_non_flagged_disease(self):
        disease = Disease.objects.create(
            name="Penyakit Surveilans Membership Test",
            code="surveillance-membership-test",
            category="emerging_reemerging",
            is_priority=False,
            is_active=True,
        )
        SurveillanceDisease.objects.create(
            program=self.program,
            disease=disease,
            official_name=disease.name,
            category=disease.category,
            is_active=True,
        )

        item = self._item(disease)

        self.assertEqual(item["name"], disease.name)
        self.assertEqual(item["attention"]["key"], "none")

    def test_highest_active_warning_wins_across_same_disease(self):
        signal = self._signal(
            code="SIG-PRI-0005",
            disease=self.tb,
            status=Signal.Status.VALIDATED,
        )
        assessment = self._assessment(
            signal,
            priority=SignalAssessment.RecommendedPriority.HIGH,
        )
        self._apply(assessment)
        warning = self._warning(
            signal,
            assessment,
            code="PD-PRI-0002",
            level=EarlyWarning.Level.HIGH,
        )

        item = self._item(self.tb)

        self.assertEqual(item["attention"]["key"], "high")
        self.assertIn(warning.code, item["attention"]["basis"])
        self.assertEqual(item["active_warning_count"], 2)
