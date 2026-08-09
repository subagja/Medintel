from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.articles.models import Article
from apps.assessments.models import (
    ArticleValidationAssessment,
    EarlyWarning,
    IntelligenceRecommendation,
    IntelligenceReport,
    IntelligenceReportHistory,
    SignalAssessment,
)
from apps.assessments.services.intelligence_report import (
    create_intelligence_report,
    eligible_report_signals,
    report_completeness,
    transition_report,
)
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ValidationStatus,
)
from apps.locations.models import Location
from apps.signals.models import Signal, SignalArticle
from apps.sources.models import Source


User = get_user_model()


class IntelligenceReportWorkspaceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="report-reviewer",
            email="report@example.com",
            password="test-password-123",
        )
        self.client.force_login(self.user)
        self.source = Source.objects.create(
            name="RRI Report Test",
            code="rri-report-test",
            domain="rri-report.example.com",
            base_url="https://rri-report.example.com",
            source_type=Source.SourceType.GOVERNMENT,
            is_verified=True,
            is_active=True,
        )
        self.disease = Disease.objects.create(
            name="Tuberkulosis Report Test",
            code="tb-report-test",
            is_active=True,
        )
        self.location = Location.objects.create(
            name="Kabupaten Tangerang Report Test",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            country_code="ID",
        )
        self.article = self._article(
            slug="linked",
            title="Dinkes mencatat 5.101 kasus Tuberkulosis",
        )
        self.unlinked_article = self._article(
            slug="unlinked",
            title="Artikel Tuberkulosis yang tidak mendukung sinyal",
        )
        self.signal = Signal.objects.create(
            code="SIG-REPORT-0001",
            title="Pelaporan Tuberkulosis di Tangerang",
            summary="Pelaporan kasus memerlukan pemantauan.",
            primary_disease=self.disease,
            primary_location=self.location,
            event_start_date=date(2026, 8, 5),
            status=Signal.Status.VALIDATED,
            priority_level=Signal.PriorityLevel.HIGH,
            confidence_level=Signal.ConfidenceLevel.HIGH,
            validated_by=self.user,
        )
        SignalArticle.objects.create(
            signal=self.signal,
            article=self.article,
            support_type=SignalArticle.SupportType.PRIMARY,
            is_primary_source=True,
            relevance_score=1.0,
        )
        self.assessment = SignalAssessment.objects.create(
            signal=self.signal,
            version=1,
            is_current=True,
            status=SignalAssessment.Status.COMPLETED,
            urgency_score=4,
            impact_score=4,
            geographic_scope_score=3,
            development_speed_score=4,
            vulnerability_score=3,
            source_reliability_score=0.85,
            information_credibility_score=0.80,
            information_completeness_score=0.75,
            evidence_consistency_score=0.80,
            priority_score=0.78,
            confidence_score=0.80,
            recommended_priority=SignalAssessment.RecommendedPriority.HIGH,
            recommended_confidence=SignalAssessment.RecommendedConfidence.HIGH,
            analytical_judgement="Situasi memerlukan verifikasi lanjutan.",
            implications="Berpotensi menambah beban layanan kesehatan.",
            recommended_actions="Konfirmasi data kepada Dinas Kesehatan.",
            assessed_by=self.user,
            completed_at=timezone.now(),
        )
        self.warning = EarlyWarning.objects.create(
            code="PD-REPORT-0001",
            signal=self.signal,
            assessment=self.assessment,
            version=1,
            is_current=True,
            level=EarlyWarning.Level.HIGH,
            confidence_level=SignalAssessment.RecommendedConfidence.HIGH,
            status=EarlyWarning.Status.ISSUED,
            title="Peringatan Dini Tuberkulosis",
            summary=self.signal.summary,
            analytical_judgement="Sinyal menunjukkan kebutuhan kewaspadaan.",
            implications="Beban layanan kesehatan dapat meningkat.",
            recommended_actions="Lakukan verifikasi dan pemantauan.",
            decision_notes="Diterbitkan setelah review.",
            issued_by=self.user,
        )
        self.recommendation = IntelligenceRecommendation.objects.create(
            code="RI-REPORT-0001",
            signal=self.signal,
            assessment=self.assessment,
            early_warning=self.warning,
            version=1,
            is_current=True,
            status=IntelligenceRecommendation.Status.APPROVED,
            urgency=IntelligenceRecommendation.Urgency.URGENT,
            action_category=(
                IntelligenceRecommendation.ActionCategory.VERIFICATION
            ),
            title="Verifikasi Tuberkulosis",
            situation_summary=self.signal.summary,
            objective="Memastikan data kasus tervalidasi.",
            recommended_action="Verifikasi data kasus kepada Dinas Kesehatan.",
            target_unit="Dinas Kesehatan Kabupaten Tangerang",
            success_indicators="Tersedia konfirmasi resmi.",
            created_by=self.user,
            approved_by=self.user,
            approved_at=timezone.now(),
        )

    def _article(self, *, slug, title):
        article = Article.objects.create(
            source=self.source,
            original_url=f"https://rri-report.example.com/{slug}",
            normalized_url=f"https://rri-report.example.com/{slug}",
            title=title,
            content_text="Dinas Kesehatan mencatat 5.101 kasus Tuberkulosis.",
            excerpt="Terdapat pelaporan kasus Tuberkulosis.",
            author="Kepala Dinas Kesehatan",
            content_hash=slug.encode("utf-8").hex()[:64].ljust(64, "0"),
            processing_status=Article.ProcessingStatus.VALIDATED,
        )
        ArticleValidationAssessment.objects.create(
            article=article,
            validation_status=(
                ArticleValidationAssessment.ValidationStatus.VALIDATED
            ),
            source_reliability=(
                ArticleValidationAssessment.SourceReliability.B
            ),
            information_credibility=(
                ArticleValidationAssessment.InformationCredibility.PROBABLY_TRUE
            ),
            evaluated_by=self.user,
        )
        ArticleDisease.objects.create(
            article=article,
            disease=self.disease,
            is_primary=True,
            validation_status=ValidationStatus.VALIDATED,
        )
        ArticleLocation.objects.create(
            article=article,
            location=self.location,
            is_primary=True,
            validation_status=ValidationStatus.VALIDATED,
        )
        ArticleFact.objects.create(
            article=article,
            disease=self.disease,
            location=self.location,
            event_date=date(2026, 8, 5),
            case_count=5101,
            fact_text="Dinas Kesehatan mencatat 5.101 kasus.",
            validation_status=ValidationStatus.VALIDATED,
            confidence_score=0.95,
        )
        return article

    def _create_report(self, **overrides):
        values = {
            "signals": [self.signal],
            "created_by": self.user,
            "kepada": "Yth. Deputi V",
            "dari": "Direktur 53",
            "tembusan": "Direktur 51",
            "hal": "Perkembangan Tuberkulosis",
            "nilai": "B2",
            "report_date": date(2026, 8, 9),
            "signature_block": "Direktur 53",
        }
        values.update(overrides)
        return create_intelligence_report(**values)

    def test_only_reportable_signal_is_offered(self):
        self.assertEqual(list(eligible_report_signals()), [self.signal])

    def test_create_report_freezes_signal_lineage_and_evidence(self):
        report = self._create_report()
        section = report.sections.get()

        self.assertEqual(report.code, "LI-2026-0001")
        self.assertEqual(section.signal, self.signal)
        self.assertEqual(section.assessment_version, 1)
        self.assertEqual(section.warning_version, 1)
        self.assertEqual(section.recommendation_version, 1)
        self.assertEqual(list(section.source_articles.all()), [self.article])
        self.assertNotIn(self.unlinked_article.title, section.indikasi_text)
        self.assertIn("5.101 kasus", section.indikasi_text)
        self.assertIn("Dinas Kesehatan Kabupaten Tangerang", section.saran_tindak_text)

    def test_create_report_writes_history(self):
        report = self._create_report()
        event = report.history.get()

        self.assertEqual(event.action, IntelligenceReportHistory.Action.CREATED)
        self.assertEqual(event.changed_by, self.user)
        self.assertEqual(event.metadata["sections"][0]["signal"], self.signal.code)

    def test_complete_report_can_follow_strict_lifecycle(self):
        report = self._create_report()
        self.assertTrue(report_completeness(report).is_complete)

        report = transition_report(
            report=report,
            target_status=IntelligenceReport.Status.FINAL,
            actor=self.user,
            notes="Substansi dan sumber telah diperiksa.",
        )
        report = transition_report(
            report=report,
            target_status=IntelligenceReport.Status.DISTRIBUTED,
            actor=self.user,
            notes="Didistribusikan kepada pimpinan.",
        )
        report = transition_report(
            report=report,
            target_status=IntelligenceReport.Status.ARCHIVED,
            actor=self.user,
            notes="Distribusi selesai dan produk diarsipkan.",
        )

        self.assertEqual(report.status, IntelligenceReport.Status.ARCHIVED)
        self.assertIsNotNone(report.finalized_at)
        self.assertIsNotNone(report.distributed_at)
        self.assertIsNotNone(report.archived_at)
        self.assertEqual(report.history.count(), 4)

    def test_lifecycle_cannot_skip_distribution(self):
        report = self._create_report()

        with self.assertRaisesMessage(ValidationError, "Urutan status wajib"):
            transition_report(
                report=report,
                target_status=IntelligenceReport.Status.ARCHIVED,
                actor=self.user,
                notes="Mencoba melewati status.",
            )

    def test_incomplete_report_cannot_be_finalized(self):
        report = self._create_report(signature_block="")

        with self.assertRaises(ValidationError):
            transition_report(
                report=report,
                target_status=IntelligenceReport.Status.FINAL,
                actor=self.user,
                notes="Mencoba finalisasi.",
            )

        report.refresh_from_db()
        self.assertEqual(report.status, IntelligenceReport.Status.DRAFT)

    def test_sidebar_opens_operational_report_workspace(self):
        url = reverse("dashboard:report-document-list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Laporan Intelijen & Arsip")
        self.assertContains(response, f'href="{url}"', html=False)
        self.assertContains(response, "Sinyal Siap")

    def test_create_view_builds_report_from_selected_signal(self):
        response = self.client.post(
            reverse("dashboard:report-document-create"),
            {
                "kepada": "Yth. Deputi V",
                "dari": "Direktur 53",
                "tembusan": "Direktur 51",
                "hal": "Perkembangan Tuberkulosis",
                "nilai": "B2",
                "report_date": "2026-08-09",
                "signature_block": "Direktur 53",
                "signals": [str(self.signal.pk)],
            },
        )

        self.assertEqual(response.status_code, 302)
        report = IntelligenceReport.objects.get()
        self.assertEqual(report.sections.get().signal, self.signal)

    def test_create_page_shows_signal_lineage(self):
        response = self.client.get(reverse("dashboard:report-document-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.signal.code)
        self.assertContains(response, self.disease.name)
        self.assertContains(response, self.location.name)

    def _edit_payload(self, report, *, action, notes=""):
        section = report.sections.get()
        return {
            "action": action,
            "header-kepada": report.kepada,
            "header-dari": report.dari,
            "header-tembusan": report.tembusan,
            "header-hal": report.hal,
            "header-nilai": report.nilai,
            "header-signature_block": report.signature_block,
            "sections-TOTAL_FORMS": "1",
            "sections-INITIAL_FORMS": "1",
            "sections-MIN_NUM_FORMS": "0",
            "sections-MAX_NUM_FORMS": "1000",
            "sections-0-id": str(section.pk),
            "sections-0-indikasi_text": section.indikasi_text,
            "sections-0-analisis_text": section.analisis_text,
            "sections-0-dampak_text": section.dampak_text,
            "sections-0-upaya_text": section.upaya_text,
            "sections-0-saran_tindak_text": section.saran_tindak_text,
            "decision-decision_notes": notes,
        }

    def test_edit_view_finalizes_complete_report_and_locks_content(self):
        report = self._create_report()
        url = reverse("dashboard:report-document-edit", args=[report.pk])

        response = self.client.post(
            url,
            self._edit_payload(
                report,
                action="finalize",
                notes="Reviewer telah memeriksa substansi dan sumber.",
            ),
        )

        self.assertEqual(response.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.status, IntelligenceReport.Status.FINAL)
        locked_response = self.client.get(url)
        self.assertContains(locked_response, "Konten terkunci")
        self.assertContains(locked_response, "Tandai Didistribusikan")

    def test_pdf_contains_report_and_sources(self):
        report = self._create_report()
        response = self.client.get(
            reverse("dashboard:report-document-export-pdf", args=[report.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn(report.code.lower(), response["Content-Disposition"])
