from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.articles.models import Article
from apps.assessments.models import (
    ArticleValidationAssessment,
    InformationEvaluation,
    SignalAssessment,
    SourceEvaluation,
)
from apps.entities.models import Disease
from apps.locations.models import Location
from apps.indicators.models import Indicator, IndicatorType
from apps.signals.models import Signal, SignalArticle, SignalIndicator
from apps.sources.models import Source


User = get_user_model()


class ThreatAssessmentWorkspaceTests(TestCase):
    def setUp(self):
        self.analyst = User.objects.create_user(
            username="threat-assessment-analyst",
            password="test-password-123",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_login(self.analyst)

        self.source = Source.objects.create(
            name="RRI Assessment Workspace",
            code="rri-assessment-workspace",
            domain="rri-assessment.example.com",
            base_url="https://rri-assessment.example.com",
            source_type=Source.SourceType.GOVERNMENT,
            is_verified=True,
            is_active=True,
        )
        self.article = Article.objects.create(
            source=self.source,
            original_url=(
                "https://rri-assessment.example.com/tb-tangerang"
            ),
            normalized_url=(
                "https://rri-assessment.example.com/tb-tangerang"
            ),
            title="Dinkes catat 5.101 kasus Tuberkulosis",
            content_text=(
                "Dinas Kesehatan mencatat 5.101 kasus Tuberkulosis "
                "di Kabupaten Tangerang."
            ),
            content_hash="w" * 64,
            processing_status=Article.ProcessingStatus.PROCESSED,
        )
        ArticleValidationAssessment.objects.create(
            article=self.article,
            validation_status=(
                ArticleValidationAssessment.ValidationStatus.VALIDATED
            ),
            source_reliability=(
                ArticleValidationAssessment.SourceReliability.B
            ),
            information_credibility=(
                ArticleValidationAssessment.InformationCredibility
                .PROBABLY_TRUE
            ),
            assessment_notes="Neraca Informasi B2 dikonfirmasi analis.",
            evaluated_by=self.analyst,
        )

        self.disease, _ = Disease.objects.get_or_create(
            name="Tuberkulosis",
            defaults={"code": "tb-assessment-workspace"},
        )
        self.location = Location.objects.create(
            name="Kabupaten Tangerang Assessment",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            country_code="ID",
        )
        self.indicator_type, _ = IndicatorType.objects.get_or_create(
            code="reported-cases-assessment-workspace",
            defaults={
                "name": "Kasus dilaporkan untuk assessment",
                "category": IndicatorType.Category.EPIDEMIOLOGICAL,
            },
        )
        self.indicator = Indicator.objects.create(
            indicator_type=self.indicator_type,
            disease=self.disease,
            location=self.location,
            event_date=date(2026, 8, 5),
            summary="Sebanyak 5.101 kasus Tuberkulosis dilaporkan.",
            status=Indicator.Status.VALIDATED,
        )
        self.signal = Signal.objects.create(
            code="SIG-2026-0001",
            title="Pelaporan 5.101 kasus Tuberkulosis",
            summary=(
                "Pelaporan kasus Tuberkulosis di Kabupaten Tangerang."
            ),
            primary_disease=self.disease,
            primary_location=self.location,
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
        SignalIndicator.objects.create(
            signal=self.signal,
            indicator=self.indicator,
            is_primary=True,
        )

        self.url = reverse("dashboard:threat-assessment")

    def assessment_payload(self):
        return {
            "action": "create_assessment",
            "signal_id": str(self.signal.pk),
            "urgency_score": "3",
            "impact_score": "3",
            "geographic_scope_score": "2",
            "development_speed_score": "2",
            "vulnerability_score": "3",
            "information_completeness_score": "0.6",
            "evidence_consistency_score": "0.8",
            "analytical_judgement": (
                "Sinyal memerlukan pemantauan dan verifikasi lanjutan."
            ),
            "implications": (
                "Berpotensi menambah beban layanan kesehatan."
            ),
            "recommended_actions": (
                "Verifikasi kepada Dinas Kesehatan dan pantau tren."
            ),
            "assumptions": (
                "Angka pada artikel menggambarkan periode berjalan."
            ),
            "limitations": (
                "Belum tersedia pembanding periode sebelumnya."
            ),
        }

    def test_sidebar_opens_assessment_workspace(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Assessment Ancaman")
        self.assertContains(
            response,
            f'href="{self.url}"',
            html=False,
        )
        self.assertContains(response, self.signal.code)

    def test_creates_assessment_from_confirmed_b2(self):
        response = self.client.post(
            self.url,
            self.assessment_payload(),
        )

        self.assertEqual(response.status_code, 302)
        assessment = SignalAssessment.objects.get(signal=self.signal)
        self.assertEqual(assessment.version, 1)
        self.assertTrue(assessment.is_current)
        self.assertEqual(assessment.source_reliability_score, 0.80)
        self.assertEqual(
            assessment.information_credibility_score,
            0.80,
        )
        self.assertEqual(
            SourceEvaluation.objects.get(signal=self.signal)
            .reliability_score,
            0.80,
        )
        self.assertEqual(
            InformationEvaluation.objects.get(signal=self.signal)
            .credibility_score,
            0.80,
        )

    def test_applies_current_recommendation_after_analyst_notes(self):
        self.client.post(self.url, self.assessment_payload())
        assessment = SignalAssessment.objects.get(signal=self.signal)

        response = self.client.post(
            self.url,
            {
                "action": "apply_recommendation",
                "signal_id": str(self.signal.pk),
                "assessment_id": str(assessment.pk),
                "decision_notes": (
                    "Rekomendasi sesuai dengan bukti dan keterbatasan."
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.signal.refresh_from_db()
        self.assertEqual(
            self.signal.priority_level,
            assessment.recommended_priority,
        )
        self.assertEqual(
            self.signal.confidence_level,
            assessment.recommended_confidence,
        )

    def test_blocks_assessment_when_information_balance_not_confirmed(self):
        balance = self.article.validation_assessment
        balance.validation_status = (
            ArticleValidationAssessment.ValidationStatus.PENDING
        )
        balance.save(update_fields=["validation_status"])

        response = self.client.post(
            self.url,
            self.assessment_payload(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "belum dikonfirmasi")
        self.assertFalse(
            SignalAssessment.objects.filter(signal=self.signal).exists()
        )

    def test_second_assessment_becomes_new_current_version(self):
        self.client.post(self.url, self.assessment_payload())
        self.client.post(self.url, self.assessment_payload())

        current = SignalAssessment.objects.get(
            signal=self.signal,
            is_current=True,
        )
        previous = SignalAssessment.objects.get(
            signal=self.signal,
            is_current=False,
        )
        self.assertEqual(current.version, 2)
        self.assertEqual(
            previous.status,
            SignalAssessment.Status.SUPERSEDED,
        )
