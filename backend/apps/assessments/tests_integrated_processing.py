"""No external API calls: access, queuing, durable bookkeeping and context safeguards."""
import hashlib
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from apps.articles.models import Article
from apps.sources.models import Source
from apps.assessments.models import IntegratedArticleProcessingJob
from apps.assessments.services.context_indicator_extraction import _clean_indicators
from apps.assessments.services.information_type_review import confirmed_information_type
from apps.assessments.services.integrated_processing import (
    enqueue_job, eligible, run_next_job, _can_auto_approve,
)


class ContextSafetyTests(SimpleTestCase):
    def test_missing_number_never_becomes_zero(self):
        self.assertEqual(_clean_indicators([], 'Imunisasi campak di Bandung'), [])

    def test_rejects_unverified_quote_and_number(self):
        data = [{'indicator_type': 'vaccinated_animals', 'value': '500',
                 'evidence_quote': '500 hewan mendapatkan vaksinasi'}]
        self.assertEqual(_clean_indicators(data, 'Kegiatan imunisasi rabies untuk masyarakat'), [])

    def test_accepts_exact_quote_without_case_count(self):
        source = 'Sebanyak 500 hewan mendapatkan vaksinasi rabies.'
        data = [{'indicator_type': 'vaccinated_animals', 'value': '500',
                 'evidence_quote': '500 hewan mendapatkan vaksinasi rabies'}]
        result = _clean_indicators(data, source)
        self.assertEqual(result[0]['value_as_reported'], '500')
        self.assertNotIn('case_count', result[0])


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class IntegratedJobTests(TestCase):
    def setUp(self):
        self.source=Source.objects.create(name='Integrated',code='integrated-test',domain='integrated.test',base_url='https://integrated.test',source_type=Source.SourceType.NATIONAL_MEDIA,is_verified=True,is_active=True)
        self.article=Article.objects.create(source=self.source,original_url='https://integrated.test/1',normalized_url='https://integrated.test/1',title='Vaksinasi campak di Kota Bandung',content_text='Vaksinasi campak berlangsung di Kota Bandung.',content_hash=hashlib.sha256(b'integrated').hexdigest(),processing_status=Article.ProcessingStatus.PROCESSED)
        U=get_user_model()
        self.master=U.objects.create_user(username='batch-master',password='test',is_superuser=True,is_staff=True)
        self.user=U.objects.create_user(username='batch-analyst',password='test')

    def test_non_superuser_cannot_start_or_read_status(self):
        self.client.force_login(self.user)
        res=self.client.post(reverse('dashboard:integrated-processing-start'),{'limit':1})
        self.assertEqual(res.status_code,403)
        res=self.client.get(reverse('dashboard:integrated-processing-status'))
        self.assertEqual(res.status_code,403)

    @patch('apps.assessments.services.integrated_processing.ready_for_ai',return_value=[])
    def test_superuser_queues_persistent_job_without_processing(self, _ready):
        self.client.force_login(self.master)
        res=self.client.post(reverse('dashboard:integrated-processing-start'),{'limit':1,'mode':'new','auto_approve':'1'})
        self.assertEqual(res.status_code,302)
        job=IntegratedArticleProcessingJob.objects.get()
        self.assertEqual(job.status,'queued')
        self.assertEqual(job.total_items,1)
        self.assertEqual(job.processed_items,0)
        self.assertTrue(job.auto_approve)
        self.assertEqual(self.article.raw_metadata or {},{})

    @patch('apps.assessments.services.integrated_processing.ready_for_ai',return_value=[])
    def test_worker_persists_progress(self, _ready):
        job=enqueue_job(self.master,limit=1)
        with patch('apps.assessments.services.integrated_processing.process_article', return_value={
                'outcome':'needs_review','category':'uncertain','extraction':'not_started'}):
            done=run_next_job()
        self.assertEqual(done.status,'completed')
        self.assertEqual(done.processed_items,1)
        self.assertEqual(done.stats['needs_review'],1)

    def test_superuser_sees_readable_last_batch_article_results(self):
        job = IntegratedArticleProcessingJob.objects.create(
            requested_by=self.master,
            mode='new', batch_size=1, total_items=1,
            status='completed', processed_items=1,
            candidate_ids=[str(self.article.pk)],
            results=[{'article_id': str(self.article.pk),
                      'category': 'prevention_response',
                      'outcome': 'needs_review',
                      'extraction': 'not_started'}],
        )
        self.client.force_login(self.master)
        response = self.client.get(reverse('dashboard:integrated-processing-status'),
                                   {'job': str(job.pk)})
        self.assertEqual(response.status_code, 200)
        item = response.json()['job']['results'][0]
        self.assertEqual(item['title'], self.article.title)
        self.assertEqual(item['outcome'], 'needs_review')
        self.assertEqual(item['detail_url'], reverse(
            'dashboard:article-extraction-detail', args=[self.article.pk]))

    def test_superuser_review_form_is_inline_below_article_row(self):
        self.client.force_login(self.master)
        response = self.client.get(reverse('dashboard:entity-extraction'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        self.assertIn('id="ex-review-row-%s"' % self.article.pk, html)
        self.assertIn('aria-controls="ex-review-row-%s"' % self.article.pk, html)
        self.assertNotIn('id="ex-classification-workspace"', html)

    def test_analyst_can_expand_own_article_review_row(self):
        from django.contrib.auth.models import Group
        group, _ = Group.objects.get_or_create(name='Analyst')
        self.user.groups.add(group)
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard:entity-extraction'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        self.assertIn('id="ex-review-row-%s"' % self.article.pk, html)
        self.assertIn('data-review-id="%s"' % self.article.pk, html)
        self.assertNotIn('id="ex-classification-workspace"', html)
        self.assertNotIn('id="medintel-integrated-panel"', html)

    def test_existing_confirmed_category_not_reprocessed(self):
        self.article.raw_metadata={'information_type_review':{'category':'prevention_response'}}
        self.article.save(update_fields=['raw_metadata'])
        self.assertFalse(eligible(self.article))
        self.assertTrue(eligible(self.article, mode='confirmed'))

    def test_unverified_title_not_enough_to_auto_approve_case(self):
        self.assertFalse(_can_auto_approve(self.article,'case_event',has_case_evidence=False))


class IntegratedQueueRegressionTests(TestCase):
    """Extra tests against 02B8A's stuck-review and double-process defects."""
    def setUp(self):
        IntegratedJobTests.setUp(self)
        from apps.assessments.services.ai_information_type import article_fingerprint
        self.article.raw_metadata = {
            'information_type_ai_suggestion': {
                'category': 'prevention_response',
                'reason': 'Kegiatan imunisasi dipakai sebagai upaya pencegahan polio.',
                'model': 'mock', 'created_at': '2026-11-11T01:00:00+00:00',
                'fingerprint': article_fingerprint(self.article),
            },
            'integrated_processing': {
                'status': 'needs_review', 'category': 'prevention_response',
            },
        }
        self.article.save(update_fields=['raw_metadata'])

    def test_confirmed_after_needs_review_same_category_is_eligible(self):
        from apps.assessments.services.information_type_review import confirm_information_type
        confirm_information_type(self.article.pk, 'prevention_response',
                                 'Imunisasi ini adalah tindakan pencegahan.', self.master)
        self.article.refresh_from_db()
        self.assertTrue(eligible(self.article, mode='confirmed'))

    def test_completed_same_confirmed_category_is_not_queued_again(self):
        from apps.assessments.services.information_type_review import confirm_information_type
        confirm_information_type(self.article.pk, 'prevention_response',
                                 'Imunisasi ini adalah tindakan pencegahan.', self.master)
        self.article.refresh_from_db()
        metadata = dict(self.article.raw_metadata)
        metadata['integrated_processing'] = {'status':'done','category':'prevention_response'}
        self.article.raw_metadata = metadata
        self.article.save(update_fields=['raw_metadata'])
        self.article.refresh_from_db()
        self.assertFalse(eligible(self.article, mode='confirmed'))

    def test_pending_retries_legacy_guarded_proposals(self):
        self.article.refresh_from_db()
        self.assertTrue(eligible(self.article, mode='pending'))
        metadata = dict(self.article.raw_metadata)
        metadata['integrated_processing']['auto_approval_attempted'] = True
        self.article.raw_metadata = metadata
        self.article.save(update_fields=['raw_metadata'])
        self.article.refresh_from_db()
        self.assertTrue(eligible(self.article, mode='pending'))
        self.assertTrue(eligible(self.article, mode='new'))

    @patch('apps.assessments.services.integrated_processing.ready_for_ai',return_value=[])
    def test_pending_requires_explicit_auto_approval(self, _ready):
        with self.assertRaisesRegex(ValueError, 'memerlukan opsi persetujuan otomatis'):
            enqueue_job(self.master,limit=1, mode='pending', auto_approve=False)

    def test_saved_ai_category_not_blocked_by_title_heuristic(self):
        self.article.refresh_from_db()
        self.assertTrue(_can_auto_approve(self.article, 'prevention_response'))
        self.assertFalse(_can_auto_approve(self.article, 'case_event'))
        self.assertFalse(_can_auto_approve(self.article, 'uncertain'))

    @patch('apps.assessments.services.integrated_processing.ready_for_ai',return_value=[])
    @patch('apps.assessments.services.integrated_processing.extract_context_indicators',
           return_value={'indicators': []})
    def test_pending_auto_saves_classification_and_continues_extraction(self, _extract, _ready):
        job = enqueue_job(self.master, limit=1, mode='pending', auto_approve=True)
        done = run_next_job()
        self.article.refresh_from_db()
        self.assertEqual(confirmed_information_type(self.article), 'prevention_response')
        self.assertEqual(self.article.raw_metadata['information_type_review']['source'],
                         'master_admin_ai_approval')
        self.assertEqual(done.stats['approved'], 1)
        self.assertEqual(done.results[0]['outcome'], 'approved')
        self.assertEqual(done.results[0]['extraction'], 'context_without_numbers')
        _extract.assert_called_once()

    @patch('apps.assessments.services.integrated_processing.ready_for_ai',return_value=[])
    def test_superuser_endpoint_always_sets_autoapprove(self, _ready):
        self.client.force_login(self.master)
        response = self.client.post(reverse('dashboard:integrated-processing-start'),
                                    {'mode':'pending', 'limit':1})
        self.assertEqual(response.status_code,302)
        self.assertTrue(IntegratedArticleProcessingJob.objects.get().auto_approve)

    @patch('apps.assessments.services.integrated_processing.ready_for_ai',return_value=[])
    @patch('apps.assessments.services.integrated_processing.review_for_extraction',
           return_value={'complete':False, 'ai_failed':False})
    def test_case_category_saved_even_with_missing_case_evidence(self, _extract, _ready):
        from apps.assessments.services.ai_information_type import article_fingerprint
        md=dict(self.article.raw_metadata)
        md['information_type_ai_suggestion']['category']='case_event'
        md['information_type_ai_suggestion']['reason']='Sumber melaporkan adanya kasus penyakit yang terjadi.'
        self.article.title='Ada kejadian penyakit campak di Bandung'
        self.article.content_text='Dinas kesehatan menerima laporan kejadian penyakit campak di wilayah Bandung.'
        md['information_type_ai_suggestion']['fingerprint']=article_fingerprint(self.article)
        md['integrated_processing']={'status':'needs_review','category':'case_event'}
        self.article.raw_metadata=md
        self.article.save(update_fields=['title','content_text','raw_metadata'])
        job=enqueue_job(self.master,mode='pending',limit=1,auto_approve=True)
        done=run_next_job()
        self.article.refresh_from_db()
        self.assertEqual(confirmed_information_type(self.article),'case_event')
        self.assertEqual(done.results[0]['extraction'],'case_incomplete')
        self.assertEqual(done.stats['approved'],1)
        _extract.assert_called_once()

    @patch('apps.assessments.services.integrated_processing.ready_for_ai',return_value=[])
    def test_uncertain_ai_remains_unconfirmed(self, _ready):
        from apps.assessments.services.ai_information_type import article_fingerprint
        md = dict(self.article.raw_metadata)
        md['information_type_ai_suggestion']['category']='uncertain'
        md['integrated_processing']={}  # fresh recommendation from an unprocessed article
        self.article.raw_metadata=md
        self.article.save(update_fields=['raw_metadata'])
        job=enqueue_job(self.master,mode='new',limit=1,auto_approve=True)
        done=run_next_job()
        self.article.refresh_from_db()
        self.assertIsNone(confirmed_information_type(self.article))
        self.assertEqual(done.stats['needs_review'],1)

    def test_rejected_validation_blocks_queue_even_if_pipeline_processed(self):
        from apps.assessments.models import ArticleValidationAssessment
        ArticleValidationAssessment.objects.create(
            article=self.article,
            validation_status=ArticleValidationAssessment.ValidationStatus.REJECTED,
        )
        self.article.refresh_from_db()
        self.assertFalse(eligible(self.article, mode='pending'))
        self.assertFalse(eligible(self.article, mode='new'))
        self.assertFalse(eligible(self.article, mode='retry'))

    def test_confirmed_analyst_decision_is_not_overwritten_by_ai_approval(self):
        from apps.assessments.services.information_type_review import confirm_information_type
        from apps.assessments.services.ai_information_type import approve_suggestion
        confirm_information_type(self.article.pk, 'risk_indicator',
                                 'Analyst menemukan bukti faktor risiko lain.', self.master)
        with self.assertRaisesRegex(ValueError, 'sudah dikonfirmasi'):
            approve_suggestion(self.article.pk, self.master)
        self.article.refresh_from_db()
        self.assertEqual(confirmed_information_type(self.article), 'risk_indicator')
