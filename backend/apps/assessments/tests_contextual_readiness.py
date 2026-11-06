"""Tes Patch 02B1: konteks tanpa jumlah kasus tidak menjadi bukti kejadian."""
import hashlib
from django.test import TestCase
from apps.articles.models import Article
from apps.assessments.models import ArticleValidationAssessment
from apps.assessments.services.extraction_dashboard import queue_rows, queue_summary
from apps.entities.models import ArticleDisease, ArticleLocation, Disease
from apps.locations.models import Location
from apps.sources.models import Source


class ContextualReadinessTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media Konteks Uji", code="media-konteks-uji",
            domain="konteks-uji.example.com", base_url="https://konteks-uji.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA, is_verified=True, is_active=True,
        )
        self.disease = Disease.objects.create(name="Campak Uji Konteks", code="campak-uji-konteks")
        self.location = Location.objects.create(name="Wilayah Uji Konteks", code="98.99",
                                               administrative_level=Location.AdministrativeLevel.CITY)

    def make_article(self, slug, kind):
        article = Article.objects.create(
            source=self.source, original_url=f"https://konteks-uji.example.com/{slug}",
            normalized_url=f"https://konteks-uji.example.com/{slug}",
            title=f"Artikel {slug}", content_text="Pelaksanaan imunisasi campak di daerah.",
            content_hash=hashlib.sha256(slug.encode()).hexdigest(),
            processing_status=Article.ProcessingStatus.PROCESSED,
        )
        ArticleValidationAssessment.objects.create(
            article=article, assessment_notes=f"[AI-assisted] [Jenis informasi: {kind}]",
        )
        return article

    def test_vaccination_without_case_count_is_review_ready_not_case_complete(self):
        article = self.make_article("vaksinasi", "prevention_response")
        ArticleDisease.objects.create(article=article, disease=self.disease)
        ArticleLocation.objects.create(article=article, location=self.location, is_primary=True)
        row = next(r for r in queue_rows() if r["article"].pk == article.pk)
        self.assertTrue(row["context_review_ready"])
        self.assertFalse(row["complete"])
        self.assertEqual(row["information_type"], "prevention_response")
        summary = queue_summary([row])
        self.assertEqual(summary["context_review_ready"], 1)
        self.assertEqual(summary["complete"], 0)

    def test_case_event_without_case_count_not_context_ready(self):
        article = self.make_article("kasus", "case_event")
        ArticleDisease.objects.create(article=article, disease=self.disease)
        ArticleLocation.objects.create(article=article, location=self.location, is_primary=True)
        row = next(r for r in queue_rows() if r["article"].pk == article.pk)
        self.assertFalse(row["context_review_ready"])
        self.assertFalse(row["complete"])

    def test_risk_article_without_location_needs_more_evidence(self):
        article = self.make_article("cakupan-turun", "risk_indicator")
        ArticleDisease.objects.create(article=article, disease=self.disease)
        row = next(r for r in queue_rows() if r["article"].pk == article.pk)
        self.assertFalse(row["context_review_ready"])
        self.assertIn("lokasi", row["context_review_label"].lower())

    def test_legacy_vaccination_candidate_not_confirmed(self):
        article = self.make_article("legacy-vaccination", "uncertain")
        article.title = "Vaksinasi campak di Kabupaten Bandung"
        article.save(update_fields=["title"])
        ArticleDisease.objects.create(article=article, disease=self.disease)
        ArticleLocation.objects.create(article=article, location=self.location, is_primary=True)
        row = next(r for r in queue_rows() if r["article"].pk == article.pk)
        self.assertEqual(row["information_type"], "uncertain")
        self.assertTrue(row["context_candidate"])
        self.assertTrue(row["context_candidate_ready"])
        self.assertFalse(row["context_review_ready"])
        self.assertFalse(row["complete"])
        self.assertEqual(queue_summary([row])["context_candidates"], 1)

    def test_case_news_with_vaccination_mention_is_not_context_candidate(self):
        article = self.make_article("mixed-news", "uncertain")
        article.title = "12 kasus campak ditemukan saat vaksinasi"
        article.save(update_fields=["title"])
        row = next(r for r in queue_rows() if r["article"].pk == article.pk)
        self.assertFalse(row["context_candidate"])
        self.assertEqual(row["information_type"], "uncertain")

    def test_confirmed_classification_overrides_title_hint(self):
        article = self.make_article("confirmed-case", "case_event")
        article.title = "Vaksinasi campak di Kabupaten Bandung"
        article.save(update_fields=["title"])
        row = next(r for r in queue_rows() if r["article"].pk == article.pk)
        self.assertEqual(row["information_type"], "case_event")
        self.assertFalse(row["context_candidate"])
