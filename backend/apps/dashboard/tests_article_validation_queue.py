import hashlib
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.articles.models import Article
from apps.assessments.models import ArticleValidationAssessment
from apps.entities.models import (
    ArticleDisease,
    ArticleLocation,
    Disease,
    ValidationStatus,
)
from apps.indicators.models import Indicator
from apps.locations.models import Location
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
class ArticleValidationQueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="article-queue-analyst",
            password="test-password-123",
            is_superuser=True,
            is_staff=True,
        )
        self.source = Source.objects.create(
            name="Media Antrean Validasi",
            code="media-antrean-validasi",
            domain="antrean-validasi.example.com",
            base_url="https://antrean-validasi.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )
        self.pending_article = self._create_article(
            slug="pending",
            title="Artikel Masih Perlu Ditinjau",
        )
        self.validated_article = self._create_article(
            slug="validated",
            title="Artikel Sudah Tervalidasi",
        )
        self.rejected_article = self._create_article(
            slug="rejected",
            title="Artikel Sudah Tidak Relevan",
        )
        ArticleValidationAssessment.objects.create(
            article=self.validated_article,
            validation_status=(
                ArticleValidationAssessment.ValidationStatus.VALIDATED
            ),
        )
        ArticleValidationAssessment.objects.create(
            article=self.rejected_article,
            validation_status=(
                ArticleValidationAssessment.ValidationStatus.REJECTED
            ),
            relevance_notes="Tidak berhubungan dengan surveilans.",
        )
        self.client.force_login(self.user)

    def _create_article(self, *, slug, title):
        return Article.objects.create(
            source=self.source,
            original_url=(
                f"https://antrean-validasi.example.com/{slug}"
            ),
            normalized_url=(
                f"https://antrean-validasi.example.com/{slug}"
            ),
            title=title,
            content_text=f"Isi artikel {slug}.",
            content_hash=hashlib.sha256(slug.encode()).hexdigest(),
            processing_status=Article.ProcessingStatus.PROCESSED,
        )

    @staticmethod
    def _listed_titles(response):
        return {article.title for article in response.context["articles"]}

    def test_default_workspace_only_contains_active_queue(self):
        response = self.client.get(
            reverse("dashboard:article-validation")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._listed_titles(response),
            {self.pending_article.title},
        )
        self.assertEqual(response.context["workspace_mode"], "queue")
        self.assertEqual(response.context["summary"]["pending"], 1)
        self.assertEqual(response.context["summary"]["validated"], 1)
        self.assertEqual(response.context["summary"]["rejected"], 1)
        self.assertContains(response, "Antrean Validasi")
        self.assertContains(response, "Riwayat Validasi")

    def test_workspace_badges_follow_active_search_filter(self):
        response = self.client.get(
            reverse("dashboard:article-validation"),
            {"workspace": "queue", "q": "tidak ditemukan"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["pending"], 1)
        self.assertEqual(
            response.context["workspace_summary"]["pending"],
            0,
        )
        self.assertEqual(
            response.context["workspace_summary"]["visible"],
            0,
        )
        self.assertContains(
            response,
            "Tidak ada artikel yang sesuai filter",
        )
        self.assertContains(response, "Reset Semua Filter")
        self.assertNotContains(
            response,
            "Antrean validasi sudah selesai",
        )

    def test_workspace_summary_keeps_global_cards_separate(self):
        response = self.client.get(
            reverse("dashboard:article-validation"),
            {
                "workspace": "queue",
                "eligibility": "eligible",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["pending"], 1)
        self.assertEqual(
            response.context["workspace_summary"]["pending"],
            1,
        )
        self.assertEqual(
            response.context["workspace_summary"]["eligible"],
            0,
        )
        self.assertEqual(
            response.context["workspace_summary"]["visible"],
            0,
        )
        self.assertContains(
            response,
            "Tidak ada artikel yang sesuai filter",
        )

    def test_history_contains_only_completed_articles(self):
        response = self.client.get(
            reverse("dashboard:article-validation"),
            {"workspace": "history"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._listed_titles(response),
            {
                self.validated_article.title,
                self.rejected_article.title,
            },
        )
        self.assertEqual(response.context["workspace_mode"], "history")
        self.assertNotIn(
            self.pending_article.title,
            self._listed_titles(response),
        )

    def test_history_status_tabs_filter_completed_articles(self):
        validated_response = self.client.get(
            reverse("dashboard:article-validation"),
            {
                "workspace": "history",
                "history_status": "validated",
            },
        )
        rejected_response = self.client.get(
            reverse("dashboard:article-validation"),
            {
                "workspace": "history",
                "history_status": "rejected",
            },
        )

        self.assertEqual(
            self._listed_titles(validated_response),
            {self.validated_article.title},
        )
        self.assertEqual(
            self._listed_titles(rejected_response),
            {self.rejected_article.title},
        )

    def test_stale_completed_article_link_falls_back_without_404(self):
        response = self.client.get(
            reverse("dashboard:article-validation"),
            {"article": str(self.validated_article.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["selected_article"].id,
            self.pending_article.id,
        )

    def test_completed_save_moves_article_out_of_queue(self):
        response = self.client.post(
            reverse("dashboard:article-validation"),
            {
                "article_id": str(self.pending_article.id),
                "action": "save_assessment",
                "workspace": "queue",
                "eligibility": "all",
                "history_status": "all",
                "validation_status": "rejected",
                "source_reliability": "B",
                "information_credibility": "2",
                "relevance_notes": "Artikel tidak relevan untuk analisis.",
                "assessment_notes": "Selesai diperiksa.",
            },
        )

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query["workspace"], ["queue"])
        self.assertNotIn("article", query)

        assessment = ArticleValidationAssessment.objects.get(
            article=self.pending_article
        )
        self.assertEqual(
            assessment.validation_status,
            ArticleValidationAssessment.ValidationStatus.REJECTED,
        )

        queue_response = self.client.get(response.url)
        self.assertNotIn(
            self.pending_article.title,
            self._listed_titles(queue_response),
        )
        history_response = self.client.get(
            reverse("dashboard:article-validation"),
            {"workspace": "history"},
        )
        self.assertIn(
            self.pending_article.title,
            self._listed_titles(history_response),
        )

    def test_status_button_saves_without_revalidating_information_tab(self):
        assessment = ArticleValidationAssessment.objects.create(
            article=self.pending_article,
            validation_status=(
                ArticleValidationAssessment.ValidationStatus.PENDING
            ),
            source_reliability=(
                ArticleValidationAssessment.SourceReliability.D
            ),
            information_credibility=(
                ArticleValidationAssessment.InformationCredibility.DOUBTFUL
            ),
            assessment_notes="",
        )

        response = self.client.post(
            reverse("dashboard:article-validation"),
            {
                "article_id": str(self.pending_article.id),
                "action": "save_validation_status",
                "workspace": "queue",
                "eligibility": "all",
                "history_status": "all",
                "validation_status": "rejected",
                "relevance_notes": "Artikel tidak relevan untuk kebutuhan.",
            },
        )

        self.assertEqual(response.status_code, 302)
        assessment.refresh_from_db()
        self.assertEqual(
            assessment.validation_status,
            ArticleValidationAssessment.ValidationStatus.REJECTED,
        )
        self.assertEqual(
            assessment.source_reliability,
            ArticleValidationAssessment.SourceReliability.D,
        )
        self.assertEqual(
            assessment.information_credibility,
            ArticleValidationAssessment.InformationCredibility.DOUBTFUL,
        )

    def test_primary_location_select_has_autocomplete_enhancement(self):
        response = self.client.get(
            reverse("dashboard:article-validation"),
            {
                "article": str(self.pending_article.id),
                "tab": "location",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-location-autocomplete")
        self.assertContains(
            response,
            'data-location-autocomplete-select="true"',
        )
        self.assertContains(
            response,
            "Ketik nama kota, kabupaten, provinsi, atau negara",
        )
        self.assertContains(response, "Lokasi belum tersedia?")
        self.assertContains(response, 'data-bs-target="#new-location-modal"')
        self.assertContains(response, "Tambah Lokasi Kejadian")

    def test_validator_can_add_missing_country_as_primary_location(self):
        response = self.client.post(
            reverse("dashboard:article-validation"),
            {
                "article_id": str(self.pending_article.id),
                "action": "create_primary_country",
                "workspace": "queue",
                "eligibility": "all",
                "history_status": "all",
                "country_name": "Israel",
                "country_code": "il",
                "country_notes": (
                    "Artikel menyebut kasus terjadi di Israel."
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("tab=location", response.url)
        country = Location.objects.get(
            administrative_level=Location.AdministrativeLevel.COUNTRY,
            country_code="IL",
        )
        relation = ArticleLocation.objects.get(
            article=self.pending_article,
            location=country,
        )
        self.assertTrue(relation.is_primary)
        self.assertEqual(country.name, "Israel")

        detail_response = self.client.get(
            reverse("dashboard:article-validation"),
            {"article": str(self.pending_article.id)},
        )
        self.assertContains(
            detail_response,
            "Luar negeri · IL",
            count=1,
        )

    def test_validator_can_add_missing_foreign_region(self):
        response = self.client.post(
            reverse("dashboard:article-validation"),
            {
                "article_id": str(self.pending_article.id),
                "action": "create_primary_location",
                "active_tab": "location",
                "workspace": "queue",
                "eligibility": "all",
                "history_status": "all",
                "location_level": Location.AdministrativeLevel.PROVINCE,
                "location_name": "California",
                "country_name": "United States",
                "country_code": "us",
                "country_notes": (
                    "Artikel menyebut kejadian terjadi di California."
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("tab=location", response.url)
        country = Location.objects.get(
            administrative_level=Location.AdministrativeLevel.COUNTRY,
            country_code="US",
        )
        region = Location.objects.get(
            name="California",
            administrative_level=Location.AdministrativeLevel.PROVINCE,
            parent=country,
            country_code="US",
        )
        relation = ArticleLocation.objects.get(
            article=self.pending_article,
            location=region,
        )
        self.assertTrue(relation.is_primary)

    def test_queue_uses_visible_server_side_pagination(self):
        Article.objects.bulk_create(
            [
                Article(
                    source=self.source,
                    original_url=(
                        "https://antrean-validasi.example.com/"
                        f"page-{index}"
                    ),
                    normalized_url=(
                        "https://antrean-validasi.example.com/"
                        f"page-{index}"
                    ),
                    title=f"Artikel Antrean Tambahan {index:02d}",
                    content_text="Isi artikel pagination.",
                    content_hash=hashlib.sha256(
                        f"page-{index}".encode()
                    ).hexdigest(),
                    processing_status=Article.ProcessingStatus.PROCESSED,
                )
                for index in range(50)
            ]
        )

        first_page = self.client.get(
            reverse("dashboard:article-validation")
        )
        second_page = self.client.get(
            reverse("dashboard:article-validation"),
            {"page": 2},
        )

        self.assertEqual(first_page.context["page_obj"].paginator.count, 51)
        self.assertEqual(len(first_page.context["articles"]), 50)
        self.assertEqual(len(second_page.context["articles"]), 1)
        self.assertContains(first_page, "Berikutnya")

    def test_relevant_article_without_numbers_validates_as_qualitative(self):
        disease = Disease.objects.create(
            name="Rabies Kualitatif",
            code="rabies-kualitatif",
        )
        location = Location.objects.create(
            name="Indonesia Kualitatif",
            code="ID-KUALITATIF",
            administrative_level=Location.AdministrativeLevel.COUNTRY,
        )
        disease_relation = ArticleDisease.objects.create(
            article=self.pending_article,
            disease=disease,
            is_primary=True,
        )
        location_relation = ArticleLocation.objects.create(
            article=self.pending_article,
            location=location,
            is_primary=True,
        )

        response = self.client.post(
            reverse("dashboard:article-validation"),
            {
                "article_id": str(self.pending_article.id),
                "action": "save_assessment",
                "workspace": "queue",
                "eligibility": "all",
                "history_status": "all",
                "validation_status": "validated",
                "source_reliability": "B",
                "information_credibility": "2",
                "assessment_notes": (
                    "Relevan sebagai informasi peningkatan rabies, "
                    "tanpa angka kasus."
                ),
                "relevance_notes": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        assessment = ArticleValidationAssessment.objects.get(
            article=self.pending_article
        )
        self.pending_article.refresh_from_db()
        disease_relation.refresh_from_db()
        location_relation.refresh_from_db()

        self.assertEqual(
            assessment.validation_status,
            ArticleValidationAssessment.ValidationStatus.VALIDATED,
        )
        self.assertEqual(
            self.pending_article.processing_status,
            Article.ProcessingStatus.VALIDATED,
        )
        self.assertEqual(
            disease_relation.validation_status,
            ValidationStatus.VALIDATED,
        )
        self.assertEqual(
            location_relation.validation_status,
            ValidationStatus.VALIDATED,
        )
        self.assertFalse(Indicator.objects.exists())

        history_response = self.client.get(
            reverse("dashboard:article-validation"),
            {"workspace": "history", "article": self.pending_article.id},
        )
        self.assertContains(history_response, "Kualitatif")

    def test_validated_article_missing_disease_and_location_shows_form_error(self):
        response = self.client.post(
            reverse("dashboard:article-validation"),
            {
                "article_id": str(self.pending_article.id),
                "action": "save_assessment",
                "workspace": "queue",
                "eligibility": "all",
                "history_status": "all",
                "validation_status": "validated",
                "source_reliability": "B",
                "information_credibility": "2",
                "assessment_notes": "Belum siap divalidasi.",
                "relevance_notes": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Artikel belum memiliki hasil ekstraksi penyakit.",
        )
        self.assertContains(
            response,
            "Artikel belum memiliki hasil ekstraksi lokasi.",
        )
        assessment = ArticleValidationAssessment.objects.get(
            article=self.pending_article
        )
        self.assertEqual(
            assessment.validation_status,
            ArticleValidationAssessment.ValidationStatus.PENDING,
        )


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
class DashboardValidationSummaryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dashboard-validation-summary",
            password="test-password-123",
            is_superuser=True,
            is_staff=True,
        )
        self.source = Source.objects.create(
            name="Media Ringkasan Validasi",
            code="media-ringkasan-validasi",
            domain="ringkasan-validasi.example.com",
            base_url="https://ringkasan-validasi.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )
        self.client.force_login(self.user)

    def _create_article(self, slug):
        return Article.objects.create(
            source=self.source,
            original_url=(
                f"https://ringkasan-validasi.example.com/{slug}"
            ),
            normalized_url=(
                f"https://ringkasan-validasi.example.com/{slug}"
            ),
            title=f"Artikel {slug}",
            content_text=f"Isi artikel {slug}.",
            content_hash=hashlib.sha256(slug.encode()).hexdigest(),
            # Sengaja sama untuk membuktikan Dashboard tidak lagi memakai
            # processing_status sebagai status keputusan validator.
            processing_status=Article.ProcessingStatus.PROCESSED,
        )

    def test_dashboard_uses_validation_assessment_as_status_source(self):
        self._create_article("tanpa-assessment")
        pending = self._create_article("pending")
        validated = self._create_article("validated")
        rejected = self._create_article("rejected")

        ArticleValidationAssessment.objects.create(
            article=pending,
            validation_status=(
                ArticleValidationAssessment.ValidationStatus.PENDING
            ),
        )
        ArticleValidationAssessment.objects.create(
            article=validated,
            validation_status=(
                ArticleValidationAssessment.ValidationStatus.VALIDATED
            ),
        )
        ArticleValidationAssessment.objects.create(
            article=rejected,
            validation_status=(
                ArticleValidationAssessment.ValidationStatus.REJECTED
            ),
        )

        response = self.client.get(reverse("dashboard:overview"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["total_articles"], 4)
        self.assertEqual(response.context["summary"]["new_articles"], 2)
        self.assertEqual(
            response.context["summary"]["validated_articles"],
            1,
        )
        self.assertEqual(
            response.context["summary"]["rejected_articles"],
            1,
        )
