"""Regression 02B8L: reconcile without overriding reviewed case facts."""
from datetime import datetime, timezone
from unittest.mock import patch
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase
from apps.articles.models import Article
from apps.entities.models import ArticleDisease, ArticleFact, ArticleLocation, Disease, ValidationStatus
from apps.entities.services.fact_extraction import (
    build_fact_candidate, distinct_case_counts_in_sentence, extract_article_facts,
    quote_location_disagrees_with_primary,
)
from apps.entities.services.fact_reconciliation import (
    assess_fact, conflicting_administrative_scopes, numeric_literal_present,
    reporting_period_hint, suggested_numeric_quote,
)
from apps.locations.models import Location
from apps.sources.models import Source


class ReconciliationPureTests(SimpleTestCase):
    def test_count_mentions_distinct_and_period_preserved(self):
        sentence = ("Hingga minggu ke-19 tahun 2026, tercatat 263 kasus "
                    "campak di Provinsi Aceh, 24 kasus di Kota Banda Aceh.")
        self.assertEqual(distinct_case_counts_in_sentence(sentence), (24, 263))
        self.assertTrue(conflicting_administrative_scopes(sentence))
        self.assertIn("minggu ke-19 tahun 2026", reporting_period_hint(sentence))

    def test_primary_city_mismatch_with_province_number(self):
        location = SimpleNamespace(name="Kota Banda Aceh")
        self.assertTrue(quote_location_disagrees_with_primary(
            "Tercatat 263 kasus campak di Provinsi Aceh.", location
        ))
        self.assertFalse(quote_location_disagrees_with_primary(
            "Tercatat 24 kasus campak di Kota Banda Aceh.", location
        ))

    def test_readonly_quote_suggestion_has_number_and_context(self):
        text="Program imunisasi. Ada 263 kasus campak di Provinsi Aceh."
        hint=suggested_numeric_quote(text,263)
        self.assertIn("263 kasus campak",hint)
        self.assertNotIn("263 kasus",suggested_numeric_quote(text,24))

    def test_numeric_support_is_exact(self):
        self.assertFalse(numeric_literal_present("188 kasus", 88))
        self.assertTrue(numeric_literal_present("88 kasus", 88))
        self.assertTrue(numeric_literal_present("1.234 kasus", 1234))

    def test_candidate_multi_count_does_not_claim_primary_location(self):
        article = SimpleNamespace(
            title="Kemenkes percepat imunisasi di Banda Aceh",
            content_text=("Hingga minggu ke-19 tahun 2026, tercatat "
                "263 kasus campak di Provinsi Aceh, dengan 24 kasus "
                "di Kota Banda Aceh."),
            published_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
        fake_location = SimpleNamespace(pk="banda-aceh")
        with patch("apps.entities.services.fact_extraction.get_primary_disease", return_value=None), \
             patch("apps.entities.services.fact_extraction.get_primary_location", return_value=fake_location):
            candidate = build_fact_candidate(article)
        self.assertEqual(candidate.case_count, 263)
        self.assertIsNone(candidate.location)


class ReconciliationDatabaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = Source.objects.create(
            name="02B8L Source", code="medintel-02b8l", domain="example-02b8l.test",
            base_url="https://example-02b8l.test", source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True, is_active=True)
        cls.disease, _ = Disease.objects.get_or_create(
            name="DBD Rekonsiliasi", defaults={"canonical_name":"Dengue", "is_active":True})
        cls.location = Location.objects.create(
            name="Kabupaten Uji Rekonsiliasi", administrative_level=Location.AdministrativeLevel.REGENCY)

    def article(self, text):
        return Article.objects.create(
            source=self.source, title="Kasus DBD di Kabupaten Uji Rekonsiliasi",
            original_url=f"https://example-02b8l.test/{Article.objects.count()+1}",
            normalized_url=f"https://example-02b8l.test/{Article.objects.count()+1}",
            content_text=text, content_hash=f"{Article.objects.count()+1:064x}",
            published_at=datetime(2026, 11, 11, tzinfo=timezone.utc))

    def relations(self, article):
        ArticleDisease.objects.create(article=article,disease=self.disease,is_primary=False,
            validation_status=ValidationStatus.VALIDATED)
        ArticleLocation.objects.create(article=article,location=self.location,is_primary=True,
            validation_status=ValidationStatus.VALIDATED)

    def test_unreviewed_duplicate_with_null_location_is_not_created(self):
        article=self.article("Sebanyak 88 kasus DBD di Kabupaten Uji Rekonsiliasi.")
        self.relations(article)
        # Candidate gets count=88 and primary location, previous row missing location.
        old=ArticleFact.objects.create(article=article,disease=self.disease,case_count=88,
            fact_text="88 kasus DBD",validation_status=ValidationStatus.UNREVIEWED)
        outcome=extract_article_facts(article)
        self.assertEqual(outcome.facts_created,0)
        self.assertEqual(ArticleFact.objects.filter(article=article).count(),1)
        old.refresh_from_db();self.assertIsNone(old.location_id)

    def test_validated_fact_never_overwritten(self):
        article=self.article("Tercatat 86 kasus DBD di Kabupaten Uji Rekonsiliasi.")
        self.relations(article)
        old=ArticleFact.objects.create(article=article,disease=self.disease,
            location=self.location,case_count=86,fact_text="Bukti ditinjau analis",
            validation_status=ValidationStatus.VALIDATED)
        extract_article_facts(article)
        old.refresh_from_db()
        self.assertEqual(old.fact_text,"Bukti ditinjau analis")
        self.assertEqual(old.validation_status,ValidationStatus.VALIDATED)

    def test_audit_flags_missing_primary_without_approving(self):
        article=self.article("Sebanyak 42 kasus DBD di Kabupaten Uji Rekonsiliasi.")
        self.relations(article)
        fact=ArticleFact.objects.create(article=article,disease=self.disease,
            case_count=42,fact_text="Layanan daerah meningkatkan kewaspadaan",
            validation_status=ValidationStatus.UNREVIEWED)
        result=assess_fact(fact)
        self.assertIn("PRIMARY_DISEASE_UNSET",result.issues)
        self.assertIn("CASE_COUNT_NOT_IN_QUOTE",result.issues)
        self.assertEqual(result.primary_disease_suggestion,self.disease.name)
        fact.refresh_from_db();self.assertEqual(fact.validation_status,ValidationStatus.UNREVIEWED)
