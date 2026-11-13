"""Regresi: ekstraksi ulang tidak menghapus hasil review penyakit."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from apps.articles.models import Article
from apps.entities.models import ArticleDisease, Disease, ExtractionMethod, ValidationStatus
from apps.entities.services.rule_based import EntityMention, extract_article_entities
from apps.sources.models import Source


class DiseaseReviewGuardTests(TestCase):
    def setUp(self):
        src = Source.objects.create(
            name="Media Pengujian Review Guard", code="review-guard-02b8j",
            domain="reviewguard.example.test", base_url="https://reviewguard.example.test",
            source_type=Source.SourceType.NATIONAL_MEDIA, is_verified=True,
        )
        self.disease = Disease.objects.create(name="DBD Review Guard", code="dbd-review-guard-02b8j")
        self.article = Article.objects.create(
            source=src, original_url="https://reviewguard.example.test/dbd",
            normalized_url="https://reviewguard.example.test/dbd",
            title="Kasus DBD Review Guard",
            content_text="DBD terjadi di wilayah uji.",
            content_hash="a" * 64,
            processing_status=Article.ProcessingStatus.PROCESSED,
        )

    def rerun(self):
        entity = EntityMention(
            entity_id=str(self.disease.pk), canonical_name=self.disease.name,
            matched_text="DBD", start=6, end=9, mention_count=3,
        )
        geo = SimpleNamespace(scope="unresolved", ambiguous_terms=())
        with patch(
            "apps.entities.services.rule_based.extract_disease_mentions",
            return_value=[entity],
        ), patch(
            "apps.entities.services.rule_based._extract_location_geolocation",
            return_value=([], geo),
        ):
            return extract_article_entities(self.article)

    def relation(self, status, method=ExtractionMethod.RULE_BASED, primary=True):
        return ArticleDisease.objects.create(
            article=self.article, disease=self.disease, is_primary=primary,
            extraction_method=method, validation_status=status,
            mention_text="Bukti dipilih pemeriksa", confidence_score=0.88,
            validation_notes="Sudah diperiksa manusia",
        )

    def test_validated_primary_is_preserved(self):
        rel = self.relation(ValidationStatus.VALIDATED)
        self.rerun()
        rel.refresh_from_db()
        self.assertTrue(rel.is_primary)
        self.assertEqual(rel.validation_status, ValidationStatus.VALIDATED)
        self.assertEqual(rel.validation_notes, "Sudah diperiksa manusia")
        self.assertEqual(rel.mention_text, "Bukti dipilih pemeriksa")

    def test_corrected_review_is_preserved(self):
        rel = self.relation(ValidationStatus.CORRECTED, ExtractionMethod.MANUAL)
        self.rerun()
        rel.refresh_from_db()
        self.assertTrue(rel.is_primary)
        self.assertEqual(rel.validation_status, ValidationStatus.CORRECTED)
        self.assertEqual(rel.extraction_method, ExtractionMethod.MANUAL)

    def test_rejected_relation_stays_rejected(self):
        rel = self.relation(ValidationStatus.REJECTED, primary=False)
        self.rerun()
        rel.refresh_from_db()
        self.assertFalse(rel.is_primary)
        self.assertEqual(rel.validation_status, ValidationStatus.REJECTED)

    def test_unreviewed_automatic_relation_can_update_confidence(self):
        rel = self.relation(ValidationStatus.UNREVIEWED, primary=False)
        result = self.rerun()
        rel.refresh_from_db()
        self.assertNotEqual(rel.confidence_score, 0.88)
        self.assertFalse(rel.is_primary)
        self.assertEqual(rel.validation_status, ValidationStatus.UNREVIEWED)
        self.assertEqual(result.diseases_updated, 1)

    def test_new_relation_remains_unreviewed(self):
        result = self.rerun()
        rel = ArticleDisease.objects.get(article=self.article, disease=self.disease)
        self.assertEqual(result.diseases_created, 1)
        self.assertFalse(rel.is_primary)
        self.assertEqual(rel.validation_status, ValidationStatus.UNREVIEWED)
