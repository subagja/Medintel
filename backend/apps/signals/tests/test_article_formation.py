from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.articles.models import Article
from apps.assessments.models import ArticleValidationAssessment
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ExtractionMethod,
    ValidationStatus,
)
from apps.locations.models import Location
from apps.indicators.models import IndicatorType
from apps.requirements.models import (
    IntelligenceRequirement,
    RequirementKeyword,
)
from apps.signals.models import Signal, SignalArticle, SignalHistory
from apps.signals.services import (
    evaluate_article_signal_candidate,
    form_signal_from_article,
)
from apps.sources.models import Source


User = get_user_model()


@override_settings(
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": (
                "django.contrib.staticfiles.storage.StaticFilesStorage"
            ),
        },
    }
)
class ArticleSignalFormationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="suryadi",
            password="test-password-123",
            is_superuser=True,
            is_staff=True,
        )
        self.source = Source.objects.create(
            name="RRI",
            code="rri-signal-test",
            domain="rri.example.test",
            base_url="https://rri.example.test",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )
        self.disease = Disease.objects.create(
            name="Tuberkulosis Signal Test",
            code="tb-signal-formation-test",
        )
        self.location = Location.objects.create(
            name="Kabupaten Tangerang Signal Test",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            country_code="ID",
        )
        self.article = Article.objects.create(
            source=self.source,
            original_url="https://rri.example.test/tangerang-tbc",
            normalized_url="https://rri.example.test/tangerang-tbc",
            title="Penderita TBC di Tangerang Capai 5.101 Kasus",
            content_text=(
                "Dinas kesehatan melaporkan 5.101 kasus TBC di "
                "Kabupaten Tangerang."
            ),
            excerpt="Dilaporkan 5.101 kasus TBC.",
            content_hash="a" * 64,
            processing_status=Article.ProcessingStatus.VALIDATED,
        )
        self.assessment = ArticleValidationAssessment.objects.create(
            article=self.article,
            validation_status=(
                ArticleValidationAssessment.ValidationStatus.VALIDATED
            ),
            source_reliability=(
                ArticleValidationAssessment.SourceReliability.B
            ),
            information_credibility=(
                ArticleValidationAssessment
                .InformationCredibility
                .PROBABLY_TRUE
            ),
            evaluated_by=self.user,
        )
        ArticleDisease.objects.create(
            article=self.article,
            disease=self.disease,
            is_primary=True,
            validation_status=ValidationStatus.VALIDATED,
        )
        ArticleLocation.objects.create(
            article=self.article,
            location=self.location,
            is_primary=True,
            validation_status=ValidationStatus.CORRECTED,
        )
        self.fact = ArticleFact.objects.create(
            article=self.article,
            disease=self.disease,
            location=self.location,
            event_date=date(2026, 8, 4),
            case_count=5101,
            trend=ArticleFact.Trend.UNKNOWN,
            fact_text="Dilaporkan 5.101 kasus TBC.",
            confidence_score=0.90,
            extraction_method=ExtractionMethod.RULE_BASED,
            validation_status=ValidationStatus.VALIDATED,
        )
        IndicatorType.objects.create(
            code="case-reported",
            name="Jumlah Kasus Dilaporkan Test",
            category=IndicatorType.Category.INFORMATION,
            default_weight=1.0,
        )
        requirement = IntelligenceRequirement.objects.create(
            code="ir-signal-formation-test",
            title="Kejadian penyakit menular",
            description="Memantau pelaporan kasus penyakit menular.",
            requirement_type=(
                IntelligenceRequirement.RequirementType.DISEASE_EVENT
            ),
            priority=IntelligenceRequirement.Priority.HIGH,
            is_active=True,
        )
        RequirementKeyword.objects.create(
            requirement=requirement,
            keyword="kasus",
            keyword_type=RequirementKeyword.KeywordType.IMPACT,
            weight=2.0,
        )

    def form_signal(self):
        return form_signal_from_article(
            article=self.article,
            analyst=self.user,
            title="Indikasi TBC di Kabupaten Tangerang",
            summary="RRI melaporkan 5.101 kasus TBC di Kabupaten Tangerang.",
            notes="Artikel Valid, Eligible, dan bernilai B2.",
        )

    def test_candidate_gate_accepts_valid_eligible_b2_article(self):
        candidate = evaluate_article_signal_candidate(self.article)

        self.assertTrue(candidate.is_ready)
        self.assertEqual(candidate.admiralty_code, "B2")
        self.assertEqual(candidate.fact, self.fact)
        self.assertEqual(candidate.primary_location, self.location)
        self.assertEqual(
            candidate.evidence_mode,
            Signal.EvidenceMode.QUANTITATIVE,
        )

    def test_valid_article_without_numeric_fact_is_qualitative_candidate(self):
        self.fact.delete()

        candidate = evaluate_article_signal_candidate(self.article)

        self.assertTrue(candidate.is_ready)
        self.assertIsNone(candidate.fact)
        self.assertTrue(candidate.is_qualitative)
        self.assertEqual(candidate.numeric_fact_label, "Bukti kualitatif")

    def test_qualitative_candidate_forms_low_priority_review_signal(self):
        self.fact.delete()

        result = form_signal_from_article(
            article=self.article,
            analyst=self.user,
            title="Indikasi kualitatif TBC di Kabupaten Tangerang",
            summary=(
                "Artikel melaporkan indikasi TBC di Kabupaten Tangerang "
                "tanpa angka kasus yang dapat divalidasi."
            ),
            notes="Penyakit dan lokasi jelas; perlu verifikasi lapangan.",
        )

        self.assertTrue(result.created)
        self.assertEqual(result.indicators_used, 0)
        self.assertEqual(
            result.signal.evidence_mode,
            Signal.EvidenceMode.QUALITATIVE,
        )
        self.assertEqual(result.signal.status, Signal.Status.NEEDS_REVIEW)
        self.assertEqual(
            result.signal.priority_level,
            Signal.PriorityLevel.LOW,
        )
        self.assertEqual(result.signal.signal_indicators.count(), 0)
        self.assertEqual(result.signal.signal_articles.count(), 1)
        self.assertEqual(
            result.signal.histories.get().metadata["evidence_mode"],
            Signal.EvidenceMode.QUALITATIVE,
        )

    def test_workspace_shows_qualitative_candidate_mode(self):
        self.fact.delete()
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard:signal-workspace"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.article.title)
        self.assertContains(response, "Kualitatif")
        self.assertContains(response, "Dasar judgement analis")

    def test_forms_signal_and_records_traceable_evidence(self):
        result = self.form_signal()

        self.assertTrue(result.created)
        self.assertFalse(result.merged)
        self.assertRegex(result.signal.code, r"^SIG-2026-\d{4}$")
        self.assertEqual(
            result.signal.title,
            "Indikasi TBC di Kabupaten Tangerang",
        )
        self.assertEqual(result.signal.created_by, self.user)
        self.assertEqual(result.signal.assigned_to, self.user)
        self.assertEqual(result.indicators_reviewed, 1)
        self.assertEqual(result.indicators_used, 1)
        self.assertEqual(SignalArticle.objects.count(), 1)
        self.assertEqual(SignalHistory.objects.count(), 1)
        history = SignalHistory.objects.get()
        self.assertEqual(history.metadata["action"], "formation")
        self.assertEqual(history.metadata["admiralty_code"], "B2")

    def test_repeated_formation_returns_same_signal(self):
        first = self.form_signal()
        second = self.form_signal()

        self.assertEqual(first.signal, second.signal)
        self.assertTrue(second.merged)
        self.assertEqual(Signal.objects.count(), 1)
        self.assertEqual(SignalArticle.objects.count(), 1)

    def test_corrected_primary_location_is_used_without_mutating_fact(self):
        contextual_location = Location.objects.create(
            name="Kota Tangerang Signal Test",
            administrative_level=Location.AdministrativeLevel.CITY,
            country_code="ID",
        )
        self.fact.location = contextual_location
        self.fact.save(update_fields=["location", "updated_at"])

        candidate = evaluate_article_signal_candidate(self.article)
        self.assertTrue(candidate.is_ready)

        result = self.form_signal()
        self.fact.refresh_from_db()

        self.assertEqual(result.signal.primary_location, self.location)
        self.assertEqual(self.fact.location, contextual_location)
        self.assertEqual(
            result.signal.signal_indicators.get().indicator.location,
            self.location,
        )

    def test_unassessed_information_is_blocked(self):
        self.assessment.source_reliability = (
            ArticleValidationAssessment.SourceReliability.F
        )
        self.assessment.information_credibility = (
            ArticleValidationAssessment
            .InformationCredibility
            .UNASSESSABLE
        )
        self.assessment.save()

        candidate = evaluate_article_signal_candidate(self.article)
        self.assertFalse(candidate.is_ready)
        with self.assertRaises(ValidationError):
            self.form_signal()

    def test_workspace_shows_candidate_and_can_form_signal(self):
        self.client.force_login(self.user)
        url = reverse("dashboard:signal-workspace")

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.article.title)
        self.assertContains(response, "B2")

        response = self.client.post(
            url,
            {
                "action": "form_signal",
                "article_id": str(self.article.pk),
                "title": "Indikasi TBC di Kabupaten Tangerang",
                "summary": (
                    "RRI melaporkan 5.101 kasus TBC di Kabupaten Tangerang."
                ),
                "formation_notes": "Artikel Valid, Eligible, dan B2.",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("mode=signals", response.url)
        self.assertEqual(Signal.objects.count(), 1)

    def test_analyst_can_confirm_signal_from_workspace(self):
        signal = self.form_signal().signal
        self.client.force_login(self.user)
        url = reverse("dashboard:signal-workspace")

        response = self.client.post(
            f"{url}?mode=signals&signal={signal.pk}",
            {
                "action": "validate_signal",
                "signal_id": str(signal.pk),
                "title": signal.title,
                "summary": signal.summary,
                "event_start_date": signal.event_start_date.isoformat(),
                "event_end_date": signal.event_end_date.isoformat(),
                "priority_level": signal.priority_level,
                "confidence_level": signal.confidence_level,
                "event_classification": (
                    Signal.EventClassification.EMERGING
                ),
                "classification_basis": (
                    "Kejadian terdeteksi pada wilayah non-endemik."
                ),
                "analyst_judgement": (
                    "Pelaporan kasus TBC memerlukan pemantauan lanjutan."
                ),
                "implication": "Beban layanan dapat meningkat.",
                "recommended_action": "Verifikasi kepada Dinas Kesehatan.",
                "information_gaps": "Belum ada data pembanding periode lalu.",
                "review_notes": "Bukti artikel dan kebutuhan intelijen sesuai.",
            },
        )

        self.assertEqual(response.status_code, 302)
        signal.refresh_from_db()
        self.assertEqual(signal.status, Signal.Status.VALIDATED)
        self.assertEqual(signal.validated_by, self.user)
        self.assertEqual(
            signal.event_classification,
            Signal.EventClassification.EMERGING,
        )
        self.assertGreaterEqual(signal.histories.count(), 3)
