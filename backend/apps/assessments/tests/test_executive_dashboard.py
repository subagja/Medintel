from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.entities.models import Disease
from apps.locations.models import Location
from apps.signals.models import Signal

from apps.assessments.models import (
    EarlyWarning,
    IntelligenceRecommendation,
    SignalAssessment,
)
from apps.assessments.services.executive_dashboard import (
    build_executive_dashboard_dataset,
)


User = get_user_model()


class ExecutiveDashboardTests(TestCase):
    def setUp(self):
        self.analyst = User.objects.create_user(
            username="executive-dashboard-analyst",
            password="test-password-123",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_login(self.analyst)
        self.province, _created = Location.objects.get_or_create(
            code="36",
            administrative_level=Location.AdministrativeLevel.PROVINCE,
            defaults={
                "name": "Banten",
                "country_code": "ID",
            },
        )
        self.location, _created = Location.objects.get_or_create(
            code="3603",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            defaults={
                "name": "Kabupaten Tangerang",
                "parent": self.province,
                "latitude": -6.1783,
                "longitude": 106.6319,
                "country_code": "ID",
            },
        )
        self.disease, _created = Disease.objects.get_or_create(
            name="Tuberkulosis",
            defaults={
                "canonical_name": "Tuberkulosis",
                "code": "tuberkulosis-executive-dashboard",
                "category": "menular_langsung",
                "is_priority": True,
                "is_active": True,
            },
        )
        Disease.objects.filter(pk=self.disease.pk).update(
            category="menular_langsung",
            is_priority=True,
            is_active=True,
        )
        self.disease.refresh_from_db()
        self.signal = self._signal("SIG-EXEC-0001")
        self.assessment = self._assessment(self.signal)
        self.url = reverse("dashboard:executive-dashboard")

    def _signal(
        self,
        code,
        *,
        status=Signal.Status.VALIDATED,
        priority=Signal.PriorityLevel.MEDIUM,
    ):
        return Signal.objects.create(
            code=code,
            title=f"Sinyal {code}",
            summary="Pelaporan kasus memerlukan verifikasi lebih lanjut.",
            primary_disease=self.disease,
            primary_location=self.location,
            event_start_date=date(2026, 8, 5),
            status=status,
            priority_level=priority,
            confidence_level=Signal.ConfidenceLevel.MEDIUM,
            analyst_judgement="Perlu pemantauan perkembangan situasi.",
            implication="Berpotensi menambah beban layanan kesehatan.",
            recommended_action="Konfirmasi kepada Dinas Kesehatan.",
            information_gaps="Belum ada data pembanding.",
        )

    def _assessment(
        self,
        signal,
        *,
        priority=SignalAssessment.RecommendedPriority.MEDIUM,
    ):
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

    def _warning(
        self,
        *,
        signal=None,
        assessment=None,
        code="PD-EXEC-0001",
        level=EarlyWarning.Level.ADVISORY,
        status=EarlyWarning.Status.ISSUED,
        current=True,
    ):
        signal = signal or self.signal
        assessment = assessment or self.assessment
        return EarlyWarning.objects.create(
            code=code,
            signal=signal,
            assessment=assessment,
            version=1,
            is_current=current,
            level=level,
            confidence_level=assessment.recommended_confidence,
            status=status,
            title=f"Peringatan Dini {self.disease.name}",
            summary="Terdapat pelaporan kasus yang memerlukan verifikasi.",
            analytical_judgement=assessment.analytical_judgement,
            implications=assessment.implications,
            recommended_actions=assessment.recommended_actions,
            information_gaps=assessment.limitations,
            decision_notes="Diterbitkan setelah konfirmasi analis.",
            issued_by=self.analyst,
        )

    def _recommendation(
        self,
        *,
        signal=None,
        assessment=None,
        warning=None,
        code="RI-EXEC-0001",
        status=IntelligenceRecommendation.Status.DRAFT,
        due_date=None,
        current=True,
    ):
        signal = signal or self.signal
        assessment = assessment or self.assessment
        return IntelligenceRecommendation.objects.create(
            code=code,
            signal=signal,
            assessment=assessment,
            early_warning=warning,
            version=1,
            is_current=current,
            status=status,
            urgency=IntelligenceRecommendation.Urgency.PRIORITY,
            action_category=(
                IntelligenceRecommendation.ActionCategory.VERIFICATION
            ),
            title=f"Rekomendasi {signal.code}",
            situation_summary="Situasi memerlukan verifikasi.",
            objective="Memperoleh konfirmasi resmi.",
            recommended_action="Koordinasi dengan Dinas Kesehatan.",
            target_unit="Unit Intelijen Medik",
            due_date=due_date,
            decision_rationale="Ditetapkan setelah telaah analis.",
            success_indicators="Tersedia konfirmasi resmi.",
            assumptions="Data merujuk periode berjalan.",
            information_gaps="Belum tersedia pembanding.",
            created_by=self.analyst,
            approved_by=(
                self.analyst
                if status
                in {
                    IntelligenceRecommendation.Status.APPROVED,
                    IntelligenceRecommendation.Status.IN_PROGRESS,
                    IntelligenceRecommendation.Status.COMPLETED,
                }
                else None
            ),
            approved_at=(
                timezone.now()
                if status
                in {
                    IntelligenceRecommendation.Status.APPROVED,
                    IntelligenceRecommendation.Status.IN_PROGRESS,
                    IntelligenceRecommendation.Status.COMPLETED,
                }
                else None
            ),
        )

    def _secondary_product(self, code):
        signal = self._signal(code)
        assessment = self._assessment(signal)
        return signal, assessment

    def test_route_and_sidebar_open_executive_dashboard(self):
        self._warning()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard Eksekutif")
        self.assertContains(response, f'href="{self.url}"', html=False)
        self.assertContains(response, "bukan penetapan KLB")

    def test_only_current_issued_warnings_are_operational(self):
        self._warning()
        signal, assessment = self._secondary_product("SIG-EXEC-0002")
        self._warning(
            signal=signal,
            assessment=assessment,
            code="PD-EXEC-0002",
            level=EarlyWarning.Level.CRITICAL,
            status=EarlyWarning.Status.CLOSED,
            current=False,
        )

        dashboard = build_executive_dashboard_dataset()

        self.assertEqual(dashboard["summary"]["active_warning_count"], 1)
        self.assertEqual(
            dashboard["summary"]["highest_level"]["key"],
            EarlyWarning.Level.ADVISORY,
        )
        self.assertEqual(dashboard["top_warning"]["code"], "PD-EXEC-0001")

    def test_unconfirmed_high_assessment_does_not_raise_executive_level(self):
        self._warning(level=EarlyWarning.Level.ADVISORY)
        signal = self._signal(
            "SIG-EXEC-0003",
            priority=Signal.PriorityLevel.HIGH,
        )
        self._assessment(
            signal,
            priority=SignalAssessment.RecommendedPriority.CRITICAL,
        )

        dashboard = build_executive_dashboard_dataset()

        self.assertEqual(
            dashboard["summary"]["highest_level"]["label"],
            "Waspada",
        )
        self.assertEqual(
            dashboard["summary"]["current_assessment_count"],
            1,
        )

    def test_recommendation_queue_separates_decision_active_and_overdue(self):
        warning = self._warning()
        self._recommendation(warning=warning)
        signal, assessment = self._secondary_product("SIG-EXEC-0004")
        self._recommendation(
            signal=signal,
            assessment=assessment,
            code="RI-EXEC-0002",
            status=IntelligenceRecommendation.Status.APPROVED,
            due_date=timezone.localdate() - timedelta(days=1),
        )
        signal, assessment = self._secondary_product("SIG-EXEC-0005")
        self._recommendation(
            signal=signal,
            assessment=assessment,
            code="RI-EXEC-0003",
            status=IntelligenceRecommendation.Status.COMPLETED,
            due_date=timezone.localdate() - timedelta(days=2),
            current=False,
        )

        dashboard = build_executive_dashboard_dataset()

        self.assertEqual(
            dashboard["summary"]["draft_recommendation_count"],
            1,
        )
        self.assertEqual(
            dashboard["summary"]["active_recommendation_count"],
            1,
        )
        self.assertEqual(
            dashboard["summary"]["overdue_recommendation_count"],
            1,
        )
        self.assertEqual(
            [row["code"] for row in dashboard["recommendation_rows"]],
            ["RI-EXEC-0002", "RI-EXEC-0001"],
        )

    def test_traceability_links_lead_back_to_operational_products(self):
        warning = self._warning()
        recommendation = self._recommendation(warning=warning)

        response = self.client.get(self.url)

        self.assertContains(response, warning.code)
        self.assertContains(response, recommendation.code)
        self.assertContains(response, reverse("dashboard:early-warning"))
        self.assertContains(
            response,
            reverse("dashboard:intelligence-recommendation"),
        )

    def test_warning_is_aggregated_to_parent_province(self):
        self._warning()

        dashboard = build_executive_dashboard_dataset()

        self.assertEqual(dashboard["summary"]["affected_province_count"], 1)
        self.assertEqual(dashboard["provinces"][0]["name"], "Banten")
        self.assertEqual(
            dashboard["provinces"][0]["level"],
            EarlyWarning.Level.ADVISORY,
        )

    def test_priority_disease_uses_products_not_article_volume(self):
        self._warning()

        dashboard = build_executive_dashboard_dataset()

        self.assertEqual(dashboard["priority_diseases"][0]["name"], "Tuberkulosis")
        self.assertEqual(
            dashboard["priority_diseases"][0]["attention"]["label"],
            "Waspada",
        )

    def test_trend_counts_analysable_signals_not_unreviewed_signal(self):
        draft_signal = self._signal(
            "SIG-EXEC-DRAFT",
            status=Signal.Status.NEEDS_REVIEW,
        )
        now = timezone.now()
        Signal.objects.filter(pk=self.signal.pk).update(first_detected_at=now)
        Signal.objects.filter(pk=draft_signal.pk).update(first_detected_at=now)

        dashboard = build_executive_dashboard_dataset(as_of=now)

        self.assertEqual(dashboard["trend"]["current_total"], 1)

    def test_empty_operational_products_render_safe_state(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["dashboard"]["summary"][
                "current_assessment_count"
            ],
            0,
        )
        self.assertEqual(
            response.context["dashboard"]["summary"][
                "priority_disease_count"
            ],
            0,
        )
        self.assertContains(response, "Tidak Ada Peringatan Aktif")
        self.assertContains(response, "Belum ada peringatan aktif")
        self.assertContains(response, "Tidak ada rekomendasi terbuka")

    def test_dashboard_displays_non_automatic_decision_boundary(self):
        response = self.client.get(self.url)

        self.assertContains(response, "tidak menetapkan KLB")
        self.assertContains(response, "tidak menjadi keputusan kebijakan otomatis")
