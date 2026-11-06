"""Regression checks for manual OSINT information-type review."""
import hashlib
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from apps.articles.models import Article
from apps.sources.models import Source
from apps.assessments.services.information_type_review import confirmed_information_type

class ManualInformationTypeReviewTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(name="Review source", code="review-type-test", domain="review-type.test", base_url="https://review-type.test", source_type=Source.SourceType.NATIONAL_MEDIA, is_verified=True, is_active=True)
        self.article = Article.objects.create(source=self.source, original_url="https://review-type.test/item", normalized_url="https://review-type.test/item", title="Vaksinasi campak", content_text="Vaksinasi campak dilakukan di Bandung.", content_hash=hashlib.sha256(b"review-type-test").hexdigest(), processing_status=Article.ProcessingStatus.PROCESSED)
        self.url = reverse('dashboard:entity-extraction-classify', args=[self.article.pk])
        User = get_user_model()
        self.admin = User.objects.create_user(username="type-review-admin", password="test", is_superuser=True, is_staff=True)
        self.viewer = User.objects.create_user(username="type-review-viewer", password="test")
        group, _ = Group.objects.get_or_create(name="Viewer")
        self.viewer.groups.add(group)

    def test_admin_can_classify_without_changing_validation(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url, {"category":"prevention_response", "reason":"Program imunisasi bukan laporan angka kasus", "scope":"context_candidate"})
        self.assertEqual(response.status_code, 302)
        self.article.refresh_from_db()
        self.assertEqual(confirmed_information_type(self.article), "prevention_response")
        self.assertEqual(self.article.processing_status, Article.ProcessingStatus.PROCESSED)

    def test_viewer_cannot_classify(self):
        self.client.force_login(self.viewer)
        response = self.client.post(self.url, {"category":"case_event", "reason":"Keterangan cukup untuk review"})
        self.assertEqual(response.status_code, 403)
        self.article.refresh_from_db()
        self.assertIsNone(confirmed_information_type(self.article))

    def test_invalid_or_short_reason_is_rejected(self):
        self.client.force_login(self.admin)
        for category, reason in (("invalid", "Alasan cukup panjang"), ("case_event", "singkat")):
            response = self.client.post(self.url, {"category":category, "reason":reason})
            self.assertEqual(response.status_code, 302)
            self.article.refresh_from_db()
            self.assertIsNone(confirmed_information_type(self.article))
