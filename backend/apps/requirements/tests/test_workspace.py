from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import Roles
from apps.articles.models import Article
from apps.assessments.models import (
    ArticleValidationAssessment,
    SignalAssessment,
)
from apps.collection.models import CollectionSession
from apps.crawlers.unified import start_unified_collection
from apps.entities.models import Disease
from apps.locations.models import Location
from apps.signals.models import Signal, SignalRequirement
from apps.sources.models import Source, SourceSeedUrl, SourceUrlPattern

from ..forms import IntelligenceRequirementForm
from ..models import (
    IntelligenceRequirement,
    RequirementArticle,
    RequirementDisease,
    RequirementHistory,
    RequirementInformationGap,
    RequirementLocation,
)
from ..services.matching import requirement_is_active_on_date
from ..services.workspace import (
    activate_requirement,
    answer_eligibility,
    answer_requirement,
    build_requirement_coverage,
    close_requirement,
    create_requirement,
    link_article,
    open_information_gap,
    resolve_information_gap,
    update_requirement,
)


User = get_user_model()


class IntelligenceRequirementWorkspaceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="requirement-workspace-admin",
            email="requirement-workspace@example.com",
            password="test-password-123",
        )
        self.analyst = self._role_user("requirement-analyst", Roles.ANALYST)
        self.reviewer = self._role_user("requirement-reviewer", Roles.REVIEWER)
        self.viewer = self._role_user("requirement-viewer", Roles.VIEWER)
        self.disease = Disease.objects.create(
            name="DBD Requirement Workspace",
            code="dbd-requirement-workspace",
        )
        self.location = Location.objects.create(
            name="Kabupaten Tangerang Requirement Workspace",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            country_code="ID",
        )
        self.source = Source.objects.create(
            name="Media Requirement Workspace",
            code="media-requirement-workspace",
            domain="requirement-workspace.example.com",
            base_url="https://requirement-workspace.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
            crawl_enabled=True,
            request_delay_seconds=0,
        )
        SourceUrlPattern.objects.create(
            source=self.source,
            pattern="/",
            pattern_type=SourceUrlPattern.PatternType.ALLOW,
            match_type=SourceUrlPattern.MatchType.PREFIX,
        )
        SourceSeedUrl.objects.create(
            source=self.source,
            url="https://requirement-workspace.example.com/kesehatan",
            seed_type=SourceSeedUrl.SeedType.LISTING,
            is_active=True,
        )

    @staticmethod
    def _role_user(username, role):
        user = User.objects.create_user(
            username=username,
            password="test-password-123",
        )
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)
        return user

    def _form(self, *, instance=None, assigned_to=None):
        return IntelligenceRequirementForm(
            data={
                "title": "Pemantauan peningkatan DBD",
                "question": (
                    "Apakah terdapat indikasi peningkatan penyebaran DBD "
                    "di Kabupaten Tangerang selama 14 hari terakhir?"
                ),
                "description": "Kebutuhan untuk mendukung kewaspadaan dini.",
                "requirement_type": (
                    IntelligenceRequirement.RequirementType.DISEASE_EVENT
                ),
                "priority": IntelligenceRequirement.Priority.HIGH,
                "valid_from": "2026-08-01",
                "valid_until": "2026-08-31",
                "assigned_to": (assigned_to or self.analyst).pk,
                "diseases": [self.disease.pk],
                "locations": [self.location.pk],
                "keywords_text": "peningkatan kasus\npenyebaran",
            },
            instance=instance,
        )

    def _draft(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)
        return create_requirement(form=form, actor=self.analyst)

    def _active(self):
        requirement = self._draft()
        return activate_requirement(
            requirement=requirement,
            actor=self.reviewer,
            notes="Sasaran dan periode telah diperiksa.",
        )

    def _signal_and_assessment(self, requirement):
        signal = Signal.objects.create(
            code=f"SIG-{str(requirement.pk)[:8]}",
            title="Peningkatan DBD di Kabupaten Tangerang",
            summary="Sinyal telah divalidasi analis.",
            primary_disease=self.disease,
            primary_location=self.location,
            status=Signal.Status.VALIDATED,
            priority_level=Signal.PriorityLevel.HIGH,
            confidence_level=Signal.ConfidenceLevel.HIGH,
        )
        SignalRequirement.objects.create(
            signal=signal,
            requirement=requirement,
            relevance_score=0.95,
            relevance_reason="Penyakit, lokasi, dan periode sesuai.",
            is_primary=True,
        )
        assessment = SignalAssessment.objects.create(
            signal=signal,
            version=1,
            is_current=True,
            status=SignalAssessment.Status.COMPLETED,
            urgency_score=4,
            impact_score=4,
            geographic_scope_score=3,
            development_speed_score=4,
            vulnerability_score=3,
            source_reliability_score=0.8,
            information_credibility_score=0.8,
            information_completeness_score=0.8,
            evidence_consistency_score=0.8,
            priority_score=0.78,
            confidence_score=0.8,
            recommended_priority=SignalAssessment.RecommendedPriority.HIGH,
            recommended_confidence=(
                SignalAssessment.RecommendedConfidence.HIGH
            ),
            analytical_judgement="Indikasi memerlukan kewaspadaan.",
            assessed_by=self.reviewer,
            completed_at=timezone.now(),
        )
        return signal, assessment

    def _article(self, slug="dbd"):
        return Article.objects.create(
            source=self.source,
            original_url=f"https://requirement-workspace.example.com/{slug}",
            normalized_url=f"https://requirement-workspace.example.com/{slug}",
            title="Dinkes melaporkan peningkatan kasus DBD",
            content_text="Terdapat peningkatan kasus DBD.",
            content_hash=(slug[0] * 64),
            processing_status=Article.ProcessingStatus.VALIDATED,
            published_at=timezone.now(),
        )

    def test_create_service_makes_inactive_draft_with_targets_and_history(self):
        requirement = self._draft()

        self.assertEqual(requirement.status, IntelligenceRequirement.Status.DRAFT)
        self.assertFalse(requirement.is_active)
        self.assertTrue(requirement.code.startswith("IR-"))
        self.assertEqual(requirement.requirement_diseases.count(), 1)
        self.assertEqual(requirement.requirement_locations.count(), 1)
        self.assertEqual(requirement.keywords.count(), 2)
        self.assertTrue(
            requirement.history.filter(
                action=RequirementHistory.Action.CREATED
            ).exists()
        )

    def test_draft_does_not_match_until_reviewer_activates_it(self):
        requirement = self._draft()
        self.assertFalse(requirement_is_active_on_date(requirement, date(2026, 8, 9)))

        requirement = activate_requirement(
            requirement=requirement,
            actor=self.reviewer,
            notes="Disetujui untuk pengumpulan.",
        )

        self.assertTrue(requirement_is_active_on_date(requirement, date(2026, 8, 9)))
        self.assertEqual(requirement.activated_by, self.reviewer)

    def test_activation_requires_targets_and_assignee(self):
        requirement = IntelligenceRequirement.objects.create(
            code="IR-NOT-READY",
            title="Belum siap",
            question="Apa yang terjadi?",
            description="Belum memiliki sasaran.",
            status=IntelligenceRequirement.Status.DRAFT,
            is_active=False,
        )

        with self.assertRaises(ValidationError) as error:
            activate_requirement(
                requirement=requirement,
                actor=self.reviewer,
                notes="Coba aktifkan.",
            )

        self.assertIn("penyakit", " ".join(error.exception.messages).lower())

    @patch("apps.crawlers.unified.transaction.on_commit")
    def test_unified_collection_is_linked_to_active_requirement(self, _on_commit):
        requirement = self._active()

        session = start_unified_collection(
            source_code=self.source.code,
            include_google_news=False,
            html_deep_scan=False,
            article_limit=10,
            candidate_limit=30,
            triggered_by=self.analyst,
            requirement=requirement,
        )

        self.assertTrue(
            session.requirement_links.filter(requirement=requirement).exists()
        )
        self.assertEqual(
            session.metadata["intelligence_requirement"], requirement.code
        )

    def test_only_validated_article_can_be_linked(self):
        requirement = self._active()
        article = self._article()

        with self.assertRaises(ValidationError):
            link_article(
                requirement=requirement,
                article=article,
                actor=self.analyst,
                reason="Relevan.",
            )

        ArticleValidationAssessment.objects.create(
            article=article,
            validation_status=(
                ArticleValidationAssessment.ValidationStatus.VALIDATED
            ),
            source_reliability=ArticleValidationAssessment.SourceReliability.B,
            information_credibility=(
                ArticleValidationAssessment.InformationCredibility.PROBABLY_TRUE
            ),
        )
        link_article(
            requirement=requirement,
            article=article,
            actor=self.analyst,
            reason="Penyakit dan wilayah sesuai.",
        )

        self.assertTrue(
            RequirementArticle.objects.filter(
                requirement=requirement, article=article
            ).exists()
        )
        self.assertEqual(build_requirement_coverage(requirement).articles, 1)

    def test_high_priority_gap_blocks_answer_until_resolved(self):
        requirement = self._active()
        self._signal_and_assessment(requirement)
        gap = open_information_gap(
            requirement=requirement,
            actor=self.analyst,
            description="Konfirmasi resmi Dinas Kesehatan belum tersedia.",
            priority=RequirementInformationGap.Priority.HIGH,
        )

        self.assertFalse(answer_eligibility(requirement).is_eligible)
        resolve_information_gap(
            gap=gap,
            actor=self.reviewer,
            notes="Konfirmasi resmi diterima.",
        )
        self.assertTrue(answer_eligibility(requirement).is_eligible)

        requirement = answer_requirement(
            requirement=requirement,
            actor=self.reviewer,
            answer_summary="Terdapat indikasi peningkatan yang memerlukan pemantauan.",
            notes="Sinyal dan assessment aktif telah diperiksa.",
        )
        self.assertEqual(requirement.status, IntelligenceRequirement.Status.ANSWERED)
        self.assertFalse(requirement.is_active)

        requirement = close_requirement(
            requirement=requirement,
            actor=self.reviewer,
            notes="Produk telah disampaikan dan kebutuhan selesai.",
        )
        self.assertEqual(requirement.status, IntelligenceRequirement.Status.CLOSED)

    def test_lifecycle_cannot_skip_from_active_to_closed(self):
        requirement = self._active()
        with self.assertRaises(ValidationError):
            close_requirement(
                requirement=requirement,
                actor=self.reviewer,
                notes="Mencoba melompati status.",
            )

    def test_answered_requirement_substance_is_locked(self):
        requirement = self._active()
        self._signal_and_assessment(requirement)
        requirement = answer_requirement(
            requirement=requirement,
            actor=self.reviewer,
            answer_summary="Kebutuhan telah dijawab.",
            notes="Assessment mencukupi.",
        )
        form = self._form(instance=requirement)
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(ValidationError):
            update_requirement(
                requirement=requirement,
                form=form,
                actor=self.analyst,
            )

    def test_workspace_route_sidebar_and_lineage_are_rendered(self):
        requirement = self._active()
        self._signal_and_assessment(requirement)
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("dashboard:intelligence-requirement"),
            {"requirement": requirement.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kebutuhan Intelijen")
        self.assertContains(response, requirement.code)
        self.assertContains(response, "Sinyal dan Produk yang Menjawab")
        self.assertContains(response, "href=\"/kebutuhan-intelijen/\"")

    def test_viewer_can_read_but_cannot_post(self):
        requirement = self._draft()
        self.client.force_login(self.viewer)

        get_response = self.client.get(
            reverse("dashboard:intelligence-requirement"),
            {"requirement": requirement.pk},
        )
        post_response = self.client.post(
            reverse("dashboard:intelligence-requirement"),
            {"action": "update", "requirement_id": requirement.pk},
        )

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(post_response.status_code, 403)

    def test_analyst_cannot_activate_requirement(self):
        requirement = self._draft()
        self.client.force_login(self.analyst)

        response = self.client.post(
            reverse("dashboard:intelligence-requirement"),
            {
                "action": "activate",
                "requirement_id": requirement.pk,
                "decision_notes": "Mencoba aktivasi tanpa reviewer.",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_collection_page_can_preselect_active_requirement(self):
        requirement = self._active()
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("dashboard:crawler-list"),
            {"requirement": requirement.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, requirement.code)
        self.assertContains(
            response,
            f'value="{requirement.pk}" selected',
            html=False,
        )

