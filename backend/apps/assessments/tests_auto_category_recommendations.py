"""Uji worker rekomendasi otomatis tanpa memanggil layanan OpenAI."""
import hashlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from apps.articles.models import Article
from apps.sources.models import Source
from apps.assessments.services.ai_information_type import article_fingerprint
from apps.assessments.services.auto_category_recommendations import eligible, generate_one, STATE_KEY
from apps.assessments.services.information_type_review import confirmed_information_type


class AutoCategoryRecommendationTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(name='Pengujian Kategori Auto', code='category-auto', domain='category-auto.test', base_url='https://category-auto.test', source_type=Source.SourceType.NATIONAL_MEDIA, is_verified=True, is_active=True)
        self.article = Article.objects.create(source=self.source, original_url='https://category-auto.test/1', normalized_url='https://category-auto.test/1', title='Imunisasi campak di Kota Bandung', content_text='Dinas kesehatan mengadakan imunisasi campak.', content_hash=hashlib.sha256(b'cat-auto').hexdigest(), processing_status=Article.ProcessingStatus.PROCESSED)

    def test_worker_generates_suggestion_without_approval(self):
        def stub(article_id):
            a = Article.objects.get(pk=article_id)
            md = dict(a.raw_metadata or {})
            md['information_type_ai_suggestion'] = {'category':'prevention_response','reason':'Kegiatan imunisasi adalah upaya pencegahan penyakit menular.','model':'mock','fingerprint':article_fingerprint(a),'created_at':timezone.now().isoformat(),'approval_status':'pending'}
            a.raw_metadata = md
            a.save(update_fields=['raw_metadata'])
        with patch('apps.assessments.services.auto_category_recommendations.request_suggestion', side_effect=stub) as ai:
            self.assertEqual(generate_one(self.article.pk), 'generated')
            ai.assert_called_once()
        self.article.refresh_from_db()
        self.assertIsNone(confirmed_information_type(self.article))
        self.assertEqual(self.article.raw_metadata[STATE_KEY]['status'], 'done')
        self.assertEqual(self.article.processing_status, Article.ProcessingStatus.PROCESSED)
        self.assertFalse(eligible(self.article))

    def test_failure_is_recorded_and_not_immediately_retried(self):
        with patch('apps.assessments.services.auto_category_recommendations.request_suggestion', side_effect=RuntimeError('mock')):
            self.assertEqual(generate_one(self.article.pk), 'failed')
        self.article.refresh_from_db()
        self.assertEqual(self.article.raw_metadata[STATE_KEY]['status'], 'failed')
        self.assertFalse(eligible(self.article))
        self.assertTrue(eligible(self.article, retry_failed=True))
        self.assertIsNone(confirmed_information_type(self.article))

    def test_rejected_article_never_attempted(self):
        self.article.processing_status = Article.ProcessingStatus.REJECTED
        self.article.save(update_fields=['processing_status'])
        with patch('apps.assessments.services.auto_category_recommendations.request_suggestion') as mock:
            self.assertEqual(generate_one(self.article.pk), 'skipped')
            mock.assert_not_called()
