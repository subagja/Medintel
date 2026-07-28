from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.articles.models import Article
from apps.assessments.models import (
    SignalAssessment,
)
from apps.assessments.services import (
    SignalAssessmentInput,
    apply_assessment_recommendation,
    create_signal_assessment,
    evaluate_information,
    evaluate_source,
)
from apps.entities.models import Disease, Location
from apps.indicators.models import (
    Indicator,
    IndicatorType,
)
from apps.requirements.models import (
    IntelligenceRequirement,
)
from apps.signals.models import (
    Signal,
    SignalArticle,
    SignalIndicator,
    SignalRequirement,
)
from apps.sources.models import Source


User = get_user_model()


class SignalAssessmentTests(TestCase):
    def setUp(self):
        self.analyst = User.objects.create_user(
            username="assessment-analyst",
            password="test-password-123",
        )

        self.source = Source.objects.create(
            name="Media Assessment",
            code="media-assessment",
            domain="assessment.example.com",
            base_url="https://assessment.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

        self.article = Article.objects.create(
            source=self.source,
            original_url=(
                "https://assessment.example.com/read/dbd"
            ),
            normalized_url=(
                "https://assessment.example.com/read/dbd"
            ),
            title="Kasus DBD meningkat",
            content_text=(
                "Kasus DBD meningkat menjadi 42 kasus."
            ),
            content_hash="i" * 64,
            processing_status=(
                Article.ProcessingStatus.PROCESSED
            ),
        )

        self.disease = Disease.objects.create(
            name="Demam Berdarah Dengue",
            code="dbd-assessment",
        )

        self.location = Location.objects.create(
            name="Kabupaten Bandung",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            country_code="ID",
        )

        self.indicator_type = IndicatorType.objects.create(
            code="case-increase-assessment",
            name="Peningkatan Kasus Assessment",
            category=(
                IndicatorType.Category.EPIDEMIOLOGICAL
            ),
        )

        self.indicator = Indicator.objects.create(
            indicator_type=self.indicator_type,
            disease=self.disease,
            location=self.location,
            event_date=date(2026, 7, 28),
            summary="Peningkatan kasus DBD.",
            status=Indicator.Status.VALIDATED,
        )

        self.requirement = (
            IntelligenceRequirement.objects.create(
                code="ir-assessment",
                title="Pemantauan peningkatan kasus",
                description="Memantau peningkatan kasus.",
                requirement_type=(
                    IntelligenceRequirement.RequirementType.DISEASE_EVENT
                ),
                priority=(
                    IntelligenceRequirement.Priority.HIGH
                ),
            )
        )

        self.signal = Signal.objects.create(
            code="SIG-2026-998001",
            title="Indikasi peningkatan kasus DBD",
            summary="Terdapat indikasi peningkatan kasus DBD.",
            primary_disease=self.disease,
            primary_location=self.location,
            status=Signal.Status.VALIDATED,
            validated_by=self.analyst,
            analyst_judgement="Sinyal relevan.",
        )

        SignalIndicator.objects.create(
            signal=self.signal,
            indicator=self.indicator,
            is_primary=True,
        )

        SignalArticle.objects.create(
            signal=self.signal,
            article=self.article,
            support_type=SignalArticle.SupportType.PRIMARY,
            is_primary_source=True,
        )

        SignalRequirement.objects.create(
            signal=self.signal,
            requirement=self.requirement,
            relevance_score=0.90,
            is_primary=True,
        )

    def create_evaluations(self):
        evaluate_source(
            signal=self.signal,
            source=self.source,
            evaluator=self.analyst,
            historical_accuracy_score=0.80,
            authority_score=0.85,
            transparency_score=0.75,
            independence_score=0.70,
            notes="Sumber cukup dapat diandalkan.",
        )

        evaluate_information(
            signal=self.signal,
            article=self.article,
            evaluator=self.analyst,
            corroboration_score=0.70,
            consistency_score=0.85,
            specificity_score=0.90,
            timeliness_score=0.90,
            supports_signal=True,
            notes="Informasi spesifik dan konsisten.",
        )

    def test_creates_signal_assessment(self):
        self.create_evaluations()

        assessment = create_signal_assessment(
            signal=self.signal,
            assessor=self.analyst,
            assessment_input=SignalAssessmentInput(
                urgency_score=4,
                impact_score=4,
                geographic_scope_score=3,
                development_speed_score=4,
                vulnerability_score=3,
                information_completeness_score=0.70,
                evidence_consistency_score=0.80,
                analytical_judgement=(
                    "Peningkatan kasus memerlukan pemantauan."
                ),
                implications=(
                    "Berpotensi meningkatkan beban layanan kesehatan."
                ),
                recommended_actions=(
                    "Lakukan verifikasi kepada sumber resmi."
                ),
                assumptions=(
                    "Angka pada artikel menggambarkan periode berjalan."
                ),
                limitations=(
                    "Belum tersedia data epidemiologis resmi."
                ),
            ),
        )

        self.assertEqual(
            assessment.version,
            1,
        )

        self.assertTrue(
            assessment.is_current
        )

        self.assertEqual(
            assessment.status,
            SignalAssessment.Status.COMPLETED,
        )

        self.assertGreater(
            assessment.priority_score,
            0,
        )

        self.assertGreater(
            assessment.confidence_score,
            0,
        )

    def test_requires_source_evaluation(self):
        evaluate_information(
            signal=self.signal,
            article=self.article,
            evaluator=self.analyst,
            corroboration_score=0.70,
            consistency_score=0.80,
            specificity_score=0.80,
            timeliness_score=0.90,
        )

        with self.assertRaises(ValidationError):
            create_signal_assessment(
                signal=self.signal,
                assessor=self.analyst,
                assessment_input=SignalAssessmentInput(
                    urgency_score=3,
                    impact_score=3,
                    geographic_scope_score=3,
                    development_speed_score=3,
                    vulnerability_score=3,
                    information_completeness_score=0.60,
                    evidence_consistency_score=0.70,
                    analytical_judgement="Judgement.",
                ),
            )

    def test_second_assessment_supersedes_first(self):
        self.create_evaluations()

        assessment_input = SignalAssessmentInput(
            urgency_score=4,
            impact_score=4,
            geographic_scope_score=3,
            development_speed_score=4,
            vulnerability_score=3,
            information_completeness_score=0.70,
            evidence_consistency_score=0.80,
            analytical_judgement="Judgement awal.",
        )

        first = create_signal_assessment(
            signal=self.signal,
            assessor=self.analyst,
            assessment_input=assessment_input,
        )

        second = create_signal_assessment(
            signal=self.signal,
            assessor=self.analyst,
            assessment_input=assessment_input,
        )

        first.refresh_from_db()

        self.assertFalse(
            first.is_current
        )

        self.assertEqual(
            first.status,
            SignalAssessment.Status.SUPERSEDED,
        )

        self.assertEqual(
            second.version,
            2,
        )

        self.assertTrue(
            second.is_current
        )

    def test_applies_assessment_recommendation(self):
        self.create_evaluations()

        assessment = create_signal_assessment(
            signal=self.signal,
            assessor=self.analyst,
            assessment_input=SignalAssessmentInput(
                urgency_score=5,
                impact_score=5,
                geographic_scope_score=4,
                development_speed_score=5,
                vulnerability_score=4,
                information_completeness_score=0.80,
                evidence_consistency_score=0.85,
                analytical_judgement=(
                    "Sinyal memerlukan perhatian segera."
                ),
                implications="Dampak dapat meningkat.",
                recommended_actions="Lakukan verifikasi segera.",
            ),
        )

        apply_assessment_recommendation(
            assessment=assessment,
            analyst=self.analyst,
            notes="Rekomendasi assessment disetujui.",
        )

        self.signal.refresh_from_db()

        self.assertEqual(
            self.signal.priority_level,
            assessment.recommended_priority,
        )

        self.assertEqual(
            self.signal.confidence_level,
            assessment.recommended_confidence,
        )