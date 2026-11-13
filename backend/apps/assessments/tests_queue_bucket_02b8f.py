"""Regression tests for classification-aware extraction counters (Patch 02B8F)."""
from django.test import TestCase, override_settings
from django.urls import reverse
from apps.assessments.services.extraction_dashboard import queue_rows, queue_summary, select_batch_ids
from apps.articles.models import Article
from apps.sources.models import Source
from apps.entities.models import ArticleDisease, ArticleLocation, ArticleFact, Disease
from apps.locations.models import Location
from django.contrib.auth import get_user_model
import hashlib


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class QueueBucketRegressionTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(name="Uji Dashboard 02B8F", code="test-02b8f",
            domain="test-02b8f.example", base_url="https://test-02b8f.example",
            source_type=Source.SourceType.NATIONAL_MEDIA, is_verified=True, is_active=True)
        self.user = get_user_model().objects.create_user(username="test-02b8f", password="test")
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)

    def make_article(self, slug, category=None):
        article = Article.objects.create(source=self.source,
            original_url=f"https://test-02b8f.example/{slug}",
            normalized_url=f"https://test-02b8f.example/{slug}",
            title=f"Artikel {slug}", content_text="Laporan tentang polio dan pencegahan.",
            content_hash=hashlib.sha256(slug.encode()).hexdigest(),
            processing_status=Article.ProcessingStatus.PROCESSED)
        if category:
            article.raw_metadata = {"information_type_review": {
                "category": category, "source": "master_admin_ai_approval", "reason": "Uji klasifikasi otomatis"}}
            article.save(update_fields=["raw_metadata"])
        return article

    def test_confirmed_prevention_moves_out_of_incomplete(self):
        self.make_article("imunisasi", "prevention_response")
        q = queue_summary(queue_rows())
        self.assertEqual(q["incomplete"], 0)
        self.assertEqual(q["contextual_articles"], 1)
        self.assertEqual(q["complete"], 0)
        self.assertEqual(q["total"], sum(q[k] for k in ("incomplete", "complete", "contextual_articles", "rejected")))
        self.assertEqual(select_batch_ids(50, scope="incomplete"), [])
        response = self.client.get(reverse("dashboard:entity-extraction"), {"scope": "context"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["extraction_page"].paginator.count, 1)

    def test_ai_suggestion_without_approval_does_not_move_bucket(self):
        article = self.make_article("suggestion")
        article.raw_metadata = {"information_type_ai_suggestion": {
            "category": "prevention_response", "reason": "Kegiatan vaksinasi", "model": "mock"}}
        article.save(update_fields=["raw_metadata"])
        q = queue_summary(queue_rows())
        self.assertEqual(q["incomplete"], 1)
        self.assertEqual(q["contextual_articles"], 0)

    def test_confirmed_case_stays_pending_if_missing_numeric_fact(self):
        self.make_article("case-incomplete", "case_event")
        q = queue_summary(queue_rows())
        self.assertEqual(q["incomplete"], 1)
        self.assertEqual(q["complete"], 0)

    def test_confirmed_context_does_not_count_as_case_even_with_numeric_fact(self):
        article = self.make_article("vaccinated-animals", "prevention_response")
        d = Disease.objects.create(name="Polio Uji", code="polio-uji-02b8f")
        l = Location.objects.create(name="Kota Contoh", code="97.88",
            administrative_level=Location.AdministrativeLevel.CITY)
        ArticleDisease.objects.create(article=article, disease=d)
        ArticleLocation.objects.create(article=article, location=l, is_primary=True)
        ArticleFact.objects.create(article=article, disease=d, location=l,
            case_count=10, fact_text="10 kasus dicatat di Kota Contoh")
        q = queue_summary(queue_rows())
        self.assertEqual(q["complete"], 0)
        self.assertEqual(q["contextual_articles"], 1)

    def test_four_counters_partition_all_articles(self):
        self.make_article("pending")
        self.make_article("risk", "risk_indicator")
        excluded = self.make_article("reject")
        excluded.processing_status = Article.ProcessingStatus.REJECTED
        excluded.save(update_fields=["processing_status"])
        q = queue_summary(queue_rows())
        self.assertEqual((q["incomplete"], q["contextual_articles"], q["rejected"]), (1, 1, 1))
        self.assertEqual(q["total"], sum(q[k] for k in ("incomplete", "complete", "contextual_articles", "rejected")))
