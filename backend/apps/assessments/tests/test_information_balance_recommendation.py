from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.articles.models import Article
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ExtractionMethod,
    Location,
    ValidationStatus,
)
from apps.sources.models import Source

from apps.assessments.models import ArticleValidationAssessment
from apps.assessments.services.information_balance import (
    recommend_information_balance,
)


class InformationBalanceRecommendationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="analyst",
        )
        self.source = self._source(
            name="RRI",
            code="rri",
            domain="rri.co.id",
            source_type=Source.SourceType.GOVERNMENT,
            is_verified=True,
        )
        self.disease, _ = Disease.objects.get_or_create(
            name="Tuberkulosis",
            defaults={"code": "tuberkulosis"},
        )
        self.location = Location.objects.create(
            name="Kabupaten Tangerang",
            code="36.03",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            country_code="ID",
            latitude=-6.18,
            longitude=106.63,
        )
        self.article = self._article(
            source=self.source,
            slug="tangerang",
            title="Penderita TBC di Tangerang Capai 5.101 Kasus",
            content=(
                "Dinas Kesehatan Kabupaten Tangerang mencatat "
                "5.101 kasus tuberkulosis."
            ),
        )
        self._complete_evidence(
            article=self.article,
            case_count=5101,
        )

    def test_verified_official_source_with_complete_evidence_recommends_b2(self):
        result = recommend_information_balance(self.article)

        self.assertEqual(result.admiralty_code, "B2")
        self.assertTrue(
            any(
                "Profil sumber telah diverifikasi" in factor
                for factor in result.supporting_factors
            )
        )
        self.assertIn("Rekomendasi sistem B2", result.suggested_notes)

    def test_unverified_source_without_history_remains_f(self):
        self.source.is_verified = False
        self.source.save(update_fields=["is_verified"])

        result = recommend_information_balance(self.article)

        self.assertEqual(result.source_reliability, "F")
        self.assertEqual(result.information_credibility, 2)

    def test_five_consistent_analyst_ratings_can_recommend_a(self):
        for index in range(5):
            prior = self._article(
                source=self.source,
                slug=f"history-{index}",
                title=f"Riwayat sumber {index}",
                content="Artikel riwayat sumber.",
            )
            ArticleValidationAssessment.objects.create(
                article=prior,
                validation_status=(
                    ArticleValidationAssessment
                    .ValidationStatus
                    .VALIDATED
                ),
                source_reliability=(
                    ArticleValidationAssessment
                    .SourceReliability
                    .A
                ),
                information_credibility=2,
                evaluated_by=self.user,
            )

        result = recommend_information_balance(self.article)

        self.assertEqual(result.source_reliability, "A")
        self.assertEqual(result.source_history_count, 5)
        self.assertEqual(result.source_history_average, 5.0)

    def test_two_validated_sources_with_aligned_number_recommend_credibility_1(self):
        for index in range(2):
            source = self._source(
                name=f"Media {index}",
                code=f"media-{index}",
                domain=f"media{index}.id",
                source_type=Source.SourceType.NATIONAL_MEDIA,
                is_verified=True,
            )
            article = self._article(
                source=source,
                slug=f"corroboration-{index}",
                title=f"Konfirmasi kasus TB {index}",
                content="Konfirmasi angka kasus TB Tangerang.",
                published_at=(
                    self.article.published_at
                    + timedelta(days=index + 1)
                ),
            )
            self._complete_evidence(
                article=article,
                case_count=5101 + index,
            )
            ArticleValidationAssessment.objects.create(
                article=article,
                validation_status=(
                    ArticleValidationAssessment
                    .ValidationStatus
                    .VALIDATED
                ),
                source_reliability="B",
                information_credibility=2,
                evaluated_by=self.user,
            )
            article.processing_status = (
                Article.ProcessingStatus.VALIDATED
            )
            article.save(update_fields=["processing_status"])

        result = recommend_information_balance(self.article)

        self.assertEqual(result.information_credibility, 1)
        self.assertEqual(len(result.corroborating_sources), 2)

    def test_missing_primary_location_recommends_credibility_6(self):
        ArticleLocation.objects.filter(
            article=self.article,
        ).update(is_primary=False)

        result = recommend_information_balance(self.article)

        self.assertEqual(result.information_credibility, 6)
        self.assertIn(
            "Belum ada tepat satu lokasi kejadian utama",
            result.limiting_factors,
        )

    def test_recommendation_does_not_write_assessment(self):
        self.assertFalse(
            ArticleValidationAssessment.objects.filter(
                article=self.article,
            ).exists()
        )

        recommend_information_balance(self.article)

        self.assertFalse(
            ArticleValidationAssessment.objects.filter(
                article=self.article,
            ).exists()
        )

    def test_validation_view_exposes_recommendation_and_requested_tab(self):
        from apps.dashboard.views import article_validation

        request = RequestFactory().get(
            "/validasi-artikel/",
            {
                "article": str(self.article.id),
                "tab": "information",
            },
        )
        request.user = self.user

        with patch(
            "apps.dashboard.views.render",
            return_value=HttpResponse(),
        ) as render_mock:
            response = article_validation(request)

        self.assertEqual(response.status_code, 200)
        context = render_mock.call_args.args[2]
        self.assertEqual(
            context["information_balance_recommendation"].admiralty_code,
            "B2",
        )
        self.assertEqual(
            context["active_validation_tab"],
            "information",
        )

    def _source(
        self,
        *,
        name,
        code,
        domain,
        source_type,
        is_verified,
    ):
        return Source.objects.create(
            name=name,
            code=code,
            domain=domain,
            base_url=f"https://{domain}/",
            source_type=source_type,
            is_verified=is_verified,
        )

    def _article(
        self,
        *,
        source,
        slug,
        title,
        content,
        published_at=None,
    ):
        return Article.objects.create(
            source=source,
            original_url=f"https://{source.domain}/{slug}",
            normalized_url=f"https://{source.domain}/{slug}",
            title=title,
            content_text=content,
            content_hash=(slug * 64)[:64],
            published_at=published_at or timezone.now(),
            processing_status=Article.ProcessingStatus.PROCESSED,
        )

    def _complete_evidence(self, *, article, case_count):
        ArticleDisease.objects.create(
            article=article,
            disease=self.disease,
            mention_text="TBC",
            extraction_method=ExtractionMethod.SYSTEM,
            is_primary=True,
        )
        ArticleLocation.objects.create(
            article=article,
            location=self.location,
            mention_text="Kabupaten Tangerang",
            extraction_method=ExtractionMethod.SYSTEM,
            is_primary=True,
        )
        ArticleFact.objects.create(
            article=article,
            disease=self.disease,
            location=self.location,
            case_count=case_count,
            fact_text=f"Tercatat {case_count} kasus.",
            extraction_method=ExtractionMethod.SYSTEM,
            validation_status=ValidationStatus.VALIDATED,
        )
