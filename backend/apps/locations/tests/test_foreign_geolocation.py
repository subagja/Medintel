import hashlib

from django.core.management import call_command
from django.test import TestCase

from apps.articles.models import Article
from apps.assessments.forms import PrimaryArticleLocationForm
from apps.entities.models import ArticleLocation
from apps.entities.services.pipeline import evaluate_article_eligibility
from apps.entities.services.rule_based import extract_article_locations
from apps.locations.geolocation import (
    resolve_global_locations,
    resolve_indonesia_locations,
)
from apps.sources.models import Source


class ForeignGeolocationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_foreign_locations", verbosity=0)
        cls.source = Source.objects.create(
            name="US CDC",
            code="us-cdc-foreign-test",
            domain="cdc.gov",
            base_url="https://www.cdc.gov",
            source_type=Source.SourceType.GOVERNMENT,
            is_verified=True,
            is_active=True,
        )

    def _article(self):
        content = (
            "Early-season detection of influenza A(H3N2) subclade K "
            "in wastewater, Colorado, USA, 2025-2026."
        )
        return Article.objects.create(
            source=self.source,
            original_url="https://cdc.gov/mmwr/foreign-location-test",
            normalized_url="https://cdc.gov/mmwr/foreign-location-test",
            title=content,
            content_text=(
                "Wastewater sequencing supported situational awareness "
                "for public health officials in Colorado."
            ),
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            processing_status=Article.ProcessingStatus.PROCESSED,
        )

    def test_global_resolver_selects_colorado_as_foreign_primary(self):
        text = (
            "Early-Season Detection of Influenza A(H3N2) "
            "in Wastewater, Colorado, USA"
        )
        result = resolve_global_locations(text, title_length=len(text))

        self.assertEqual(result.scope, "foreign")
        self.assertIsNotNone(result.primary)
        self.assertEqual(result.primary.location_name, "Colorado")
        self.assertEqual(result.primary.country_code, "US")

    def test_domestic_resolver_remains_indonesia_only(self):
        result = resolve_indonesia_locations(
            "Influenza A(H3N2) terdeteksi di Colorado, USA."
        )

        self.assertEqual(result.scope, "unresolved")
        self.assertEqual(result.mentions, ())

    def test_pipeline_persists_foreign_location_and_form_can_select_it(self):
        article = self._article()
        result = extract_article_locations(article)
        relation = ArticleLocation.objects.get(article=article)
        form = PrimaryArticleLocationForm(article=article)
        eligibility = evaluate_article_eligibility(article)

        self.assertEqual(result.geolocation_scope, "foreign")
        self.assertEqual(relation.location.name, "Colorado")
        self.assertEqual(relation.location.country_code, "US")
        self.assertTrue(relation.is_primary)
        self.assertIn(
            relation.location_id,
            form.fields["primary_location"].queryset.values_list(
                "id", flat=True
            ),
        )
        self.assertTrue(eligibility.has_location)
