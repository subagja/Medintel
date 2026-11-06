"""Regression checks for AI-assisted information type approval rights."""
import hashlib
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from apps.sources.models import Source
from apps.articles.models import Article
from apps.assessments.services.ai_information_type import suggestion_for, article_fingerprint
from apps.assessments.services.information_type_review import confirmed_information_type
from django.utils import timezone

class AIInformationTypeRoleTests(TestCase):
    def setUp(self):
        self.source=Source.objects.create(name="AI category",code="ai-category-test",domain="ai-category.test",base_url="https://ai-category.test",source_type=Source.SourceType.NATIONAL_MEDIA,is_verified=True,is_active=True)
        self.article=Article.objects.create(source=self.source,original_url="https://ai-category.test/1",normalized_url="https://ai-category.test/1",title="Vaksinasi campak di Bandung",content_text="Pemerintah menyelenggarakan vaksinasi campak di Bandung.",content_hash=hashlib.sha256(b"ai-cat").hexdigest(),processing_status=Article.ProcessingStatus.PROCESSED)
        User=get_user_model()
        self.master=User.objects.create_user(username="ai-type-master",password="test",is_superuser=True,is_staff=True)
        self.analyst=User.objects.create_user(username="ai-type-analyst",password="test")
        group,_=Group.objects.get_or_create(name="Analyst");self.analyst.groups.add(group)
        self.viewer=User.objects.create_user(username="ai-type-viewer",password="test")
        group,_=Group.objects.get_or_create(name="Viewer");self.viewer.groups.add(group)
        self.url=reverse("dashboard:entity-extraction-ai-suggest",args=[self.article.pk])
        self.approve_url=reverse("dashboard:entity-extraction-ai-approve",args=[self.article.pk])
    def _save_proposal(self):
        d={"category":"prevention_response","reason":"Program vaksinasi adalah informasi pencegahan, bukan jumlah kasus.","model":"mock","created_at":timezone.now().isoformat(),"fingerprint":article_fingerprint(self.article),"approval_status":"pending"}
        self.article.raw_metadata={"information_type_ai_suggestion":d}
        self.article.save(update_fields=["raw_metadata"])
        return d
    def test_analyst_gets_ai_recommendation_without_auto_approval(self):
        self.client.force_login(self.analyst)
        with patch("apps.assessments.services.ai_information_type.request_suggestion") as mock:
            response=self.client.post(self.url,{"approve":"0","scope":"all"})
        self.assertEqual(response.status_code,302)
        mock.assert_called_once()
        self.assertIsNone(confirmed_information_type(self.article))
    def test_analyst_cannot_auto_approve(self):
        self._save_proposal();self.client.force_login(self.analyst)
        self.assertEqual(self.client.post(self.approve_url,{}).status_code,403)
        self.assertIsNone(confirmed_information_type(self.article))
    def test_master_approves_stored_proposal(self):
        self._save_proposal();self.client.force_login(self.master)
        response=self.client.post(self.approve_url,{"scope":"all"})
        self.assertEqual(response.status_code,302)
        self.article.refresh_from_db()
        self.assertEqual(confirmed_information_type(self.article),"prevention_response")
        self.assertEqual(self.article.processing_status,Article.ProcessingStatus.PROCESSED)
    def test_viewer_cannot_generate(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(self.url,{}).status_code,403)
    def test_stale_proposal_cannot_be_approved(self):
        self._save_proposal();self.article.title="Konten artikel diperbarui";self.article.save(update_fields=["title"])
        self.client.force_login(self.master)
        self.client.post(self.approve_url,{"scope":"all"})
        self.article.refresh_from_db()
        self.assertIsNone(confirmed_information_type(self.article))
