from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.articles.models import Article
from apps.entities.models import Disease
from apps.locations.models import Location
from apps.indicators.models import Indicator, IndicatorType
from apps.requirements.models import IntelligenceRequirement
from apps.signals.models import (
    Signal,
    SignalArticle,
    SignalHistory,
    SignalIndicator,
    SignalRequirement,
)
from apps.signals.services import (
    assign_signal,
    close_signal,
    correct_signal,
    escalate_signal,
    reject_signal,
    start_signal_review,
    validate_signal,
)
from apps.sources.models import Source


User = get_user_model()


class SignalLifecycleTests(TestCase):
    def setUp(self):
        self.analyst = User.objects.create_user(
            username="signal-reviewer",
            password="test-password-123",
        )

        self.assigner = User.objects.create_user(
            username="signal-assigner",
            password="test-password-123",
        )

        self.source = Source.objects.create(
            name="Media Lifecycle",
            code="media-lifecycle",
            domain="lifecycle.example.com",
            base_url="https://lifecycle.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

        self.article = Article.objects.create(
            source=self.source,
            original_url=(
                "https://lifecycle.example.com/read/dbd"
            ),
            normalized_url=(
                "https://lifecycle.example.com/read/dbd"
            ),
            title="Kasus DBD meningkat",
            content_text="Kasus DBD meningkat.",
            content_hash="h" * 64,
            processing_status=Article.ProcessingStatus.PROCESSED,
        )

        self.disease, _ = Disease.objects.get_or_create(
            name="Demam Berdarah Dengue",
            defaults={"code": "dbd-lifecycle"},
        )

        self.location = Location.objects.create(
            name="Kabupaten Bandung",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            country_code="ID",
        )

        self.indicator_type = IndicatorType.objects.create(
            code="case-increase-lifecycle",
            name="Peningkatan Kasus Lifecycle",
            category=IndicatorType.Category.EPIDEMIOLOGICAL,
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
                code="ir-lifecycle",
                title="Pemantauan peningkatan penyakit",
                description="Pemantauan peningkatan kasus.",
                requirement_type=(
                    IntelligenceRequirement.RequirementType.DISEASE_EVENT
                ),
                priority=IntelligenceRequirement.Priority.HIGH,
            )
        )

        self.signal = Signal.objects.create(
            code="SIG-2026-999001",
            title="Indikasi peningkatan DBD",
            summary="Terdapat indikasi peningkatan DBD.",
            primary_disease=self.disease,
            primary_location=self.location,
            event_start_date=date(2026, 7, 28),
            event_end_date=date(2026, 7, 28),
            status=Signal.Status.NEEDS_REVIEW,
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

    def test_assigns_signal_to_analyst(self):
        assign_signal(
            signal=self.signal,
            analyst=self.analyst,
            assigned_by=self.assigner,
            notes="Ditugaskan untuk ditinjau.",
        )

        self.signal.refresh_from_db()

        self.assertEqual(
            self.signal.assigned_to,
            self.analyst,
        )

        self.assertEqual(
            SignalHistory.objects.count(),
            1,
        )

    def test_starts_review(self):
        start_signal_review(
            signal=self.signal,
            reviewer=self.analyst,
            notes="Review dimulai.",
        )

        self.signal.refresh_from_db()

        self.assertEqual(
            self.signal.status,
            Signal.Status.UNDER_REVIEW,
        )

        self.assertEqual(
            self.signal.assigned_to,
            self.analyst,
        )

    def test_validates_signal(self):
        start_signal_review(
            signal=self.signal,
            reviewer=self.analyst,
        )

        validate_signal(
            signal=self.signal,
            reviewer=self.analyst,
            judgement=(
                "Sinyal menunjukkan peningkatan kejadian "
                "yang perlu dipantau."
            ),
            implication=(
                "Berpotensi meningkatkan kebutuhan respons kesehatan."
            ),
            recommended_action=(
                "Lakukan verifikasi melalui sumber resmi."
            ),
            information_gaps=(
                "Belum tersedia data resmi jumlah kasus."
            ),
            notes="Sinyal sesuai bukti pendukung.",
        )

        self.signal.refresh_from_db()

        self.assertEqual(
            self.signal.status,
            Signal.Status.VALIDATED,
        )

        self.assertEqual(
            self.signal.validated_by,
            self.analyst,
        )

        self.assertTrue(
            self.signal.analyst_judgement
        )

    def test_reject_requires_reason(self):
        with self.assertRaises(ValidationError):
            reject_signal(
                signal=self.signal,
                reviewer=self.analyst,
                notes="",
            )

    def test_escalates_validated_signal(self):
        start_signal_review(
            signal=self.signal,
            reviewer=self.analyst,
        )

        validate_signal(
            signal=self.signal,
            reviewer=self.analyst,
            judgement="Sinyal relevan.",
            notes="Valid.",
        )

        escalate_signal(
            signal=self.signal,
            reviewer=self.analyst,
            notes="Memerlukan perhatian segera.",
            priority_level=Signal.PriorityLevel.HIGH,
        )

        self.signal.refresh_from_db()

        self.assertEqual(
            self.signal.status,
            Signal.Status.ESCALATED,
        )

        self.assertEqual(
            self.signal.priority_level,
            Signal.PriorityLevel.HIGH,
        )

    def test_closes_escalated_signal(self):
        start_signal_review(
            signal=self.signal,
            reviewer=self.analyst,
        )

        validate_signal(
            signal=self.signal,
            reviewer=self.analyst,
            judgement="Sinyal relevan.",
        )

        escalate_signal(
            signal=self.signal,
            reviewer=self.analyst,
            notes="Dieskalasi.",
        )

        close_signal(
            signal=self.signal,
            reviewer=self.analyst,
            notes="Sinyal selesai ditindaklanjuti.",
        )

        self.signal.refresh_from_db()

        self.assertEqual(
            self.signal.status,
            Signal.Status.CLOSED,
        )

        self.assertIsNotNone(
            self.signal.closed_at
        )

    def test_invalid_transition_is_rejected(self):
        with self.assertRaises(ValidationError):
            close_signal(
                signal=self.signal,
                reviewer=self.analyst,
                notes="Ditutup.",
            )
