import hashlib

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.articles.models import Article
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
)
from apps.locations.models import Location
from apps.sources.models import Source


User = get_user_model()


class EntityExtractionLiveStatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="entity-extraction-live",
            password="test-password-123",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.source = Source.objects.create(
            name="Media Ekstraksi Live",
            code="media-ekstraksi-live",
            domain="ekstraksi-live.example.com",
            base_url="https://ekstraksi-live.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )
        self.disease = Disease.objects.create(
            name="Penyakit Uji Ekstraksi",
            code="penyakit-uji-ekstraksi",
        )
        self.location = Location.objects.create(
            name="Lokasi Uji Ekstraksi",
            code="99.99",
            administrative_level=Location.AdministrativeLevel.CITY,
        )

    def _create_article(self, slug):
        return Article.objects.create(
            source=self.source,
            original_url=f"https://ekstraksi-live.example.com/{slug}",
            normalized_url=f"https://ekstraksi-live.example.com/{slug}",
            title=f"Artikel {slug}",
            content_text=f"Isi artikel {slug}.",
            content_hash=hashlib.sha256(slug.encode()).hexdigest(),
            processing_status=Article.ProcessingStatus.PROCESSED,
        )

    def test_incomplete_count_uses_entity_completeness(self):
        complete = self._create_article("lengkap")
        self._create_article("belum-lengkap")

        ArticleDisease.objects.create(
            article=complete,
            disease=self.disease,
        )
        ArticleLocation.objects.create(
            article=complete,
            location=self.location,
        )
        ArticleFact.objects.create(
            article=complete,
            disease=self.disease,
            location=self.location,
            case_count=10,
        )

        response = self.client.get(
            reverse("dashboard:entity-extraction-status")
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["total_articles"], 2)
        self.assertEqual(payload["summary"]["pending_count"], 1)
        self.assertEqual(payload["summary"]["with_disease"], 1)
        self.assertEqual(payload["summary"]["with_location"], 1)
        self.assertEqual(payload["summary"]["with_fact"], 1)

    def test_status_endpoint_returns_cached_batch_progress(self):
        batch_id = "batch-test-live"
        cache.set(
            f"entity-extraction-batch:{batch_id}",
            {
                "state": "running",
                "total": 50,
                "processed": 12,
                "failed": 1,
            },
            timeout=60,
        )

        response = self.client.get(
            reverse("dashboard:entity-extraction-status"),
            {"batch": batch_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["batch"],
            {
                "state": "running",
                "total": 50,
                "processed": 12,
                "failed": 1,
            },
        )
