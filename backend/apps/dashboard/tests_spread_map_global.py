from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.articles.models import Article
from apps.assessments.models import ArticleValidationAssessment
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ValidationStatus,
)
from apps.locations.models import Location
from apps.sources.models import Source


User = get_user_model()


class SpreadMapGlobalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="spread-map-global-admin",
            password="test-password-123",
            email="global-map@example.com",
        )
        self.client.force_login(self.user)
        self.source = Source.objects.create(
            name="US CDC",
            code="us-cdc-global-map",
            domain="cdc.gov",
            base_url="https://www.cdc.gov",
            source_type=Source.SourceType.GOVERNMENT,
            is_verified=True,
        )
        self.disease = Disease.objects.create(
            name="Penyakit Uji Peta Global",
            canonical_name="Global Map Test Disease",
            code="penyakit-uji-peta-global",
        )
        self.country = Location.objects.create(
            name="United States",
            code="US",
            administrative_level=Location.AdministrativeLevel.COUNTRY,
            country_code="US",
            latitude=39.828300,
            longitude=-98.579500,
        )
        self.colorado = Location.objects.create(
            name="Colorado",
            code="US-CO",
            administrative_level=Location.AdministrativeLevel.PROVINCE,
            parent=self.country,
            country_code="US",
            latitude=39.550100,
            longitude=-105.782100,
        )
        self.url = reverse("dashboard:spread-map-data")

    def _validated_article(self, *, suffix, day, case_count=None):
        published_at = timezone.make_aware(
            datetime(2026, 8, day, 10, 0, 0)
        )
        article = Article.objects.create(
            source=self.source,
            original_url=f"https://www.cdc.gov/article-{suffix}",
            normalized_url=f"https://www.cdc.gov/article-{suffix}",
            title=f"Colorado influenza surveillance update {suffix}",
            content_text="Influenza surveillance in Colorado, USA.",
            content_hash=(suffix * 64)[:64],
            published_at=published_at,
            processing_status=Article.ProcessingStatus.PROCESSED,
        )
        ArticleDisease.objects.create(
            article=article,
            disease=self.disease,
            is_primary=True,
            validation_status=ValidationStatus.VALIDATED,
        )
        ArticleLocation.objects.create(
            article=article,
            location=self.colorado,
            is_primary=True,
            validation_status=ValidationStatus.VALIDATED,
        )
        ArticleValidationAssessment.objects.create(
            article=article,
            validation_status=(
                ArticleValidationAssessment.ValidationStatus.VALIDATED
            ),
        )
        ArticleFact.objects.create(
            article=article,
            disease=self.disease,
            location=self.colorado,
            event_date=published_at.date(),
            case_count=case_count,
            fact_text="Wastewater surveillance result.",
            validation_status=(
                ValidationStatus.VALIDATED
                if case_count is not None
                else ValidationStatus.UNREVIEWED
            ),
        )
        return article

    def _latest_global_entry(self):
        response = self.client.get(
            self.url,
            {
                "scope": "global",
                "mode": "cumulative",
                "disease": self.disease.code,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        location_id = str(self.colorado.id)
        return payload, payload["timeline"][-1]["locations"][location_id]

    def test_qualitative_foreign_article_appears_without_case_count(self):
        self._validated_article(suffix="qualitative", day=1)

        payload, entry = self._latest_global_entry()

        self.assertEqual(payload["scope"], "global")
        self.assertEqual(entry["article_count"], 1)
        self.assertIsNone(entry["latest_case_count"])
        self.assertEqual(entry["severity"], "qualitative")
        self.assertEqual(
            payload["locations"][str(self.colorado.id)]["country_code"],
            "US",
        )

    def test_latest_case_figure_is_not_summed_between_updates(self):
        self._validated_article(suffix="first", day=1, case_count=100)
        self._validated_article(suffix="update", day=8, case_count=120)

        _payload, entry = self._latest_global_entry()

        self.assertEqual(entry["article_count"], 2)
        self.assertEqual(entry["latest_case_count"], 120)

    def test_foreign_article_does_not_enter_domestic_endpoint(self):
        self._validated_article(suffix="foreign-only", day=1, case_count=75)

        response = self.client.get(
            self.url,
            {
                "scope": "domestic",
                "mode": "cumulative",
                "disease": self.disease.code,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["timeline"], [])

    def test_page_contains_global_scope_and_metric_explanation(self):
        self._validated_article(suffix="page", day=1)

        response = self.client.get(reverse("dashboard:spread-map"))

        self.assertContains(response, "data-map-scope=\"global\"")
        self.assertContains(response, "Angka Kasus Terbaru")
        self.assertContains(response, "Akumulasi Artikel")
