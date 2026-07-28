from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.articles.models import Article
from apps.entities.models import (
    ArticleFact,
    Disease,
    ExtractionMethod,
    Location,
    ValidationStatus,
)
from apps.indicators.models import (
    Indicator,
    IndicatorEvidence,
    IndicatorType,
)
from apps.requirements.models import (
    IntelligenceRequirement,
    RequirementDisease,
    RequirementIndicator,
    RequirementKeyword,
    RequirementLocation,
)
from apps.requirements.services import (
    match_indicator_to_requirements,
)
from apps.sources.models import Source


User = get_user_model()


class RequirementMatchingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="requirement-analyst",
            password="test-password-123",
        )

        self.source = Source.objects.create(
            name="Media Requirement",
            code="media-requirement",
            domain="requirement.example.com",
            base_url="https://requirement.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

        self.article = Article.objects.create(
            source=self.source,
            original_url=(
                "https://requirement.example.com/read/dbd"
            ),
            normalized_url=(
                "https://requirement.example.com/read/dbd"
            ),
            title="Kasus DBD meningkat",
            content_text=(
                "Kasus DBD meningkat menjadi 42 kasus."
            ),
            content_hash="g" * 64,
            processing_status=(
                Article.ProcessingStatus.PROCESSED
            ),
        )

        self.disease = Disease.objects.create(
            name="Demam Berdarah Dengue",
            code="dbd-requirement",
        )

        self.indonesia = Location.objects.create(
            name="Indonesia",
            administrative_level=(
                Location.AdministrativeLevel.COUNTRY
            ),
            country_code="ID",
        )

        self.jawa_barat = Location.objects.create(
            name="Jawa Barat",
            administrative_level=(
                Location.AdministrativeLevel.PROVINCE
            ),
            parent=self.indonesia,
            country_code="ID",
        )

        self.bandung = Location.objects.create(
            name="Kabupaten Bandung",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            parent=self.jawa_barat,
            country_code="ID",
        )

        self.indicator_type = IndicatorType.objects.create(
            code="case-increase",
            name="Peningkatan Kasus",
            category=(
                IndicatorType.Category.EPIDEMIOLOGICAL
            ),
        )

        self.fact = ArticleFact.objects.create(
            article=self.article,
            disease=self.disease,
            location=self.bandung,
            event_date=date(2026, 7, 28),
            case_count=42,
            trend=ArticleFact.Trend.INCREASING,
            fact_text=(
                "Kasus DBD meningkat menjadi 42 kasus."
            ),
            extraction_method=ExtractionMethod.RULE_BASED,
            validation_status=ValidationStatus.VALIDATED,
        )

        self.indicator = Indicator.objects.create(
            indicator_type=self.indicator_type,
            disease=self.disease,
            location=self.bandung,
            event_date=date(2026, 7, 28),
            value=42,
            unit="kasus",
            direction=Indicator.Direction.INCREASING,
            summary=(
                "Terindikasi peningkatan kasus "
                "Demam Berdarah Dengue "
                "di Kabupaten Bandung."
            ),
            confidence_score=0.90,
            status=Indicator.Status.VALIDATED,
            validated_by=self.user,
        )

        IndicatorEvidence.objects.create(
            indicator=self.indicator,
            article=self.article,
            article_fact=self.fact,
            evidence_text=self.fact.fact_text,
            is_primary_evidence=True,
        )

        self.requirement = (
            IntelligenceRequirement.objects.create(
                code="ir-test-001",
                title=(
                    "Peningkatan penyakit menular "
                    "berpotensi KLB"
                ),
                description=(
                    "Memantau peningkatan penyakit menular."
                ),
                requirement_type=(
                    IntelligenceRequirement.RequirementType.DISEASE_EVENT
                ),
                priority=(
                    IntelligenceRequirement.Priority.HIGH
                ),
                is_active=True,
            )
        )

        RequirementDisease.objects.create(
            requirement=self.requirement,
            disease=self.disease,
            priority_weight=10.0,
            is_primary=True,
        )

        RequirementLocation.objects.create(
            requirement=self.requirement,
            location=self.indonesia,
            priority_weight=10.0,
            include_descendants=True,
        )

        RequirementKeyword.objects.create(
            requirement=self.requirement,
            keyword="peningkatan",
            keyword_type=(
                RequirementKeyword.KeywordType.TREND
            ),
            weight=3.0,
        )

    def test_matches_validated_indicator(self):
        result = match_indicator_to_requirements(
            self.indicator
        )

        self.assertEqual(
            len(result.matches_created),
            1,
        )

        relation = RequirementIndicator.objects.get()

        self.assertEqual(
            relation.requirement,
            self.requirement,
        )

        self.assertGreaterEqual(
            relation.relevance_score,
            0.30,
        )

    def test_location_descendant_matches_country(self):
        result = match_indicator_to_requirements(
            self.indicator
        )

        self.assertEqual(
            len(result.matches_created),
            1,
        )

        relation = RequirementIndicator.objects.get()

        self.assertIn(
            "Lokasi sesuai",
            relation.relevance_reason,
        )

    def test_unreviewed_indicator_is_skipped(self):
        self.indicator.status = (
            Indicator.Status.NEEDS_REVIEW
        )

        self.indicator.save(
            update_fields=["status"]
        )

        result = match_indicator_to_requirements(
            self.indicator
        )

        self.assertEqual(
            len(result.matches_created),
            0,
        )

        self.assertIn(
            "belum divalidasi",
            result.skipped_reason,
        )

    def test_matching_is_idempotent(self):
        first_result = match_indicator_to_requirements(
            self.indicator
        )

        second_result = match_indicator_to_requirements(
            self.indicator
        )

        self.assertEqual(
            len(first_result.matches_created),
            1,
        )

        self.assertEqual(
            len(second_result.matches_created),
            0,
        )

        self.assertEqual(
            len(second_result.matches_updated),
            1,
        )

        self.assertEqual(
            RequirementIndicator.objects.count(),
            1,
        )