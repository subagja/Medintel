"""MEDINTEL 02B8A: persistent superuser-authorized classification + extraction.

This is a *processing* workflow, NOT medical validation. AI proposals, approved
categories and structured evidence have deliberately separate provenance.
"""
from __future__ import annotations

import importlib.util
import logging
import os
from datetime import timedelta
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.articles.models import Article
from apps.assessments.models import IntegratedArticleProcessingJob
from .ai_information_type import request_suggestion, suggestion_for, approve_suggestion
from .information_type_review import confirmed_information_type, classification_allowed
from .contextual_readiness import context_candidate_from_title, _PREVENTION, _RISK, _CASE_EVENT
from .extraction_dashboard import evidence_snapshot, review_for_extraction
from .context_indicator_extraction import extract_context_indicators

logger = logging.getLogger(__name__)

VALID_MODES = frozenset({'new', 'retry', 'confirmed', 'pending'})
ACTIVE_STATUSES = ('queued', 'running')
STALE_AFTER = timedelta(minutes=15)
DEFAULT_STATS = {
    'suggested': 0, 'approved': 0, 'needs_review': 0,
    'case_complete': 0, 'case_incomplete': 0,
    'context_extracted': 0, 'context_without_numbers': 0,
    'errors': 0, 'skipped': 0, 'confirmed_extracted': 0,
}


def ready_for_ai():
    """Safe configuration check; never return the secret itself."""
    missing = []
    if not os.environ.get('OPENAI_API_KEY'):
        missing.append('OPENAI_API_KEY')
    if importlib.util.find_spec('openai') is None:
        missing.append('paket openai')
    return missing


def _last_state(article):
    state = (article.raw_metadata or {}).get('integrated_processing') or {}
    return state if isinstance(state, dict) else {}


def eligible(article, mode='new'):
    if not classification_allowed(article):
        return False
    verified = confirmed_information_type(article)
    state = _last_state(article)
    previous = state.get('status')
    if mode == 'retry':
        return previous == 'failed'
    if mode == 'confirmed':
        # A confirmation after needs_review must resume extraction, even if the
        # category has not changed; only a completed run is considered done.
        if verified is None or verified == 'uncertain' or previous == 'running':
            return False
        return not (previous == 'done' and state.get('category') == verified)
    if mode == 'pending':
        # Resume previously suggested articles once with Superuser-approved
        # safeguards; do not silently retry uncertain or failed guard results.
        proposal = suggestion_for(article)
        return bool(verified is None and previous == 'needs_review'
                    and proposal and proposal['category'] in
                    ('case_event', 'risk_indicator', 'prevention_response'))
    # New AI classification only: do not override approved/manual decisions.
    return verified is None and (previous not in ('failed', 'running', 'done', 'needs_review')
            or (previous == 'needs_review' and bool(suggestion_for(article))
                and suggestion_for(article)['category'] in ('case_event', 'risk_indicator', 'prevention_response')))


def select_candidate_ids(limit, mode):
    selected = []
    for article in (Article.objects.exclude(processing_status=Article.ProcessingStatus.REJECTED)
                    .select_related('validation_assessment').order_by('-created_at')
                    .iterator(chunk_size=100)):
        if eligible(article, mode=mode):
            selected.append(str(article.pk))
            if len(selected) >= limit:
                break
    return selected


def enqueue_job(user, *, limit=5, mode='new', auto_approve=False, ai_case_evidence=False):
    if not user or not user.is_authenticated or not user.is_superuser:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied('Hanya Master Admin yang dapat memulai batch terpadu.')
    if mode not in VALID_MODES:
        raise ValueError('Jenis antrean tidak dikenali.')
    if mode == 'pending' and not auto_approve:
        raise ValueError('Antrean rekomendasi tertunda memerlukan opsi persetujuan otomatis.')
    if not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError('Jumlah artikel harus 1–50.')
    missing = ready_for_ai()
    if missing:
        raise ValueError('AI belum siap: ' + ', '.join(missing))
    with transaction.atomic():
        if IntegratedArticleProcessingJob.objects.filter(status__in=ACTIVE_STATUSES).exists():
            raise ValueError('Masih ada batch terpadu aktif. Selesaikan atau pulihkan dahulu.')
        ids = select_candidate_ids(limit, mode)
        if not ids:
            raise ValueError('Tidak ada artikel pada antrean yang dipilih.')
        try:
            return IntegratedArticleProcessingJob.objects.create(
                requested_by=user, mode=mode, batch_size=limit, total_items=len(ids),
                candidate_ids=ids, auto_approve=bool(auto_approve),
                ai_case_evidence=bool(ai_case_evidence), stats=dict(DEFAULT_STATS),
            )
        except IntegrityError as exc:
            raise ValueError('Batch aktif terdeteksi. Periksa status pekerjaan.') from exc


def _record_article_state(article_id, *, status, job_id, error_type='', category='', note='', auto_approval_attempted=None):
    # The article update is atomic and keeps other raw_metadata writes intact.
    with transaction.atomic():
        article = Article.objects.select_for_update().get(pk=article_id)
        md = dict(article.raw_metadata or {})
        md['integrated_processing'] = {
            'status': status, 'job_id': str(job_id),
            'updated_at': timezone.now().isoformat(),
            'category': category, 'error_type': error_type, 'note': (note or '')[:200],
        }
        if auto_approval_attempted is not None:
            md['integrated_processing']['auto_approval_attempted'] = bool(auto_approval_attempted)
        article.raw_metadata = md
        article.save(update_fields=['raw_metadata', 'updated_at'])


def _can_auto_approve(article, category, *, has_case_evidence=False):
    """Authorize *category* persistence from a current AI proposal.

    Category approval is not article validation, confirmation of case counts,
    or a claim that extracted indicators have been verified. Those assessments
    have separate statuses. Existing manual decisions are never overwritten.
    """
    if category not in ('case_event', 'risk_indicator', 'prevention_response'):
        return False
    proposal = suggestion_for(article)
    return bool(proposal and proposal.get('category') == category
                and (article.title or article.content_text))


def process_article(article_id, job):
    """One article. Never turns context quantities into ArticleFact.case_count."""
    article = Article.objects.select_related('source', 'validation_assessment').get(pk=article_id)
    if not classification_allowed(article):
        return {'outcome': 'skipped', 'category': '', 'extraction': 'skipped'}

    # If a Reviewer/Analyst previously confirmed the category, re-use that
    # decision and continue extraction without requesting or approving AI again.
    confirmed_category = confirmed_information_type(article)
    if confirmed_category is not None:
        category = confirmed_category
    else:
        proposal = suggestion_for(article)
        if not proposal:
            proposal = request_suggestion(article.pk)
        category = proposal['category']
    article.refresh_from_db()

    if category == 'uncertain':
        _record_article_state(article_id, status='needs_review', job_id=job.pk,
                              category=category, note='AI belum dapat menentukan jenis informasi',
                              auto_approval_attempted=job.auto_approve)
        return {'outcome': 'needs_review', 'category': category, 'extraction': 'not_started'}

    # Save classification first. Missing case counts must not block the
    # classification decision. The Superuser explicitly authorizes this policy
    # when starting the batch; it is *not* a manual review by that Superuser.
    approved = False
    if confirmed_category is None and (
            job.auto_approve and job.requested_by_id and job.requested_by
            and job.requested_by.is_superuser
            and _can_auto_approve(article, category)):
        approve_suggestion(article_id, job.requested_by)
        approved = True

    category_confirmed = approved or confirmed_category is not None
    extraction = 'not_started'
    ai_failed = False
    if category_confirmed and category == 'case_event':
        reviewed = review_for_extraction(article_id, with_ai=job.ai_case_evidence, force=False)
        extraction = 'case_complete' if reviewed['complete'] else 'case_incomplete'
        ai_failed = bool(reviewed.get('ai_failed'))
    elif category_confirmed and category in ('risk_indicator', 'prevention_response'):
        context = extract_context_indicators(article_id, category)
        extraction = 'context_extracted' if context['indicators'] else 'context_without_numbers'

    outcome = ('confirmed_extracted' if confirmed_category is not None else
               'approved' if approved else 'needs_review')
    _record_article_state(article_id, status='done' if category_confirmed else 'needs_review',
                          job_id=job.pk, category=category,
                          note='Kategori lama, ekstraksi lanjutan' if confirmed_category is not None else
                          'Disetujui otomatis' if approved else 'Perlu keputusan pemeriksa',
                          auto_approval_attempted=bool(job.auto_approve and confirmed_category is None))
    return {'outcome': outcome, 'category': category, 'extraction': extraction,
            'ai_evidence_failed': ai_failed}


def _claim(job_id, *, recover_stale=False):
    with transaction.atomic():
        job = IntegratedArticleProcessingJob.objects.select_for_update().get(pk=job_id)
        if job.status == 'queued':
            job.status = 'running'
            job.started_at = job.started_at or timezone.now()
            job.updated_at = timezone.now()
            job.save(update_fields=['status', 'started_at', 'updated_at'])
            return job
        if recover_stale and job.status == 'running' and job.updated_at < timezone.now() - STALE_AFTER:
            job.updated_at = timezone.now()
            job.save(update_fields=['updated_at'])
            return job
        return None


def run_job(job_id, *, recover_stale=False):
    job = _claim(job_id, recover_stale=recover_stale)
    if job is None:
        return None
    if ready_for_ai():
        job.status = 'failed'
        job.error_message = 'Konfigurasi AI tidak siap di proses worker.'
        job.completed_at = timezone.now()
        job.save(update_fields=['status','error_message','completed_at','updated_at'])
        return job
    for item_id in job.candidate_ids:
        # Re-read before each article to support recovery with persisted offsets.
        job.refresh_from_db()
        if item_id in job.finished_ids:
            continue
        with transaction.atomic():
            progress = IntegratedArticleProcessingJob.objects.select_for_update().get(pk=job_id)
            progress.current_article_id = item_id
            progress.updated_at = timezone.now()
            progress.save(update_fields=['current_article_id', 'updated_at'])
        try:
            result = process_article(UUID(item_id), job)
            error_type = ''
        except Exception as exc:
            logger.exception('Batch terpadu gagal artikel=%s jenis=%s', item_id, type(exc).__name__)
            result = {'outcome': 'error', 'category': '', 'extraction': 'error'}
            error_type = type(exc).__name__
            try:
                _record_article_state(UUID(item_id), status='failed', job_id=job.pk,
                                      error_type=error_type)
            except Exception:
                logger.exception('Gagal menyimpan status artikel=%s', item_id)
        with transaction.atomic():
            current = IntegratedArticleProcessingJob.objects.select_for_update().get(pk=job_id)
            if item_id in current.finished_ids:
                continue
            stats = dict(current.stats or DEFAULT_STATS)
            stats['suggested'] = stats.get('suggested', 0) + int(result['outcome'] in ('approved', 'needs_review'))
            counter = {'approved':'approved', 'needs_review':'needs_review', 'error':'errors', 'skipped':'skipped', 'confirmed_extracted':'confirmed_extracted'}[result['outcome']]
            stats[counter] = stats.get(counter, 0) + 1
            if result['extraction'] in ('case_complete','case_incomplete','context_extracted','context_without_numbers'):
                stats[result['extraction']] = stats.get(result['extraction'], 0) + 1
            details = list(current.results or [])
            details.append({'article_id': item_id, 'category': result['category'],
                            'outcome': result['outcome'], 'extraction': result['extraction'],
                            'error_type': error_type})
            current.stats = stats
            current.results = details[-50:]
            current.finished_ids = [*current.finished_ids, item_id]
            current.processed_items = len(current.finished_ids)
            current.current_article_id = ''
            current.updated_at = timezone.now()
            current.save(update_fields=['stats','results','finished_ids','processed_items',
                                        'current_article_id','updated_at'])
    with transaction.atomic():
        done = IntegratedArticleProcessingJob.objects.select_for_update().get(pk=job_id)
        if done.status == 'running':
            done.status = 'completed_with_errors' if (done.stats or {}).get('errors') else 'completed'
            done.completed_at = timezone.now()
            done.updated_at = timezone.now()
            done.save(update_fields=['status','completed_at','updated_at'])
    return done


def run_next_job(*, recover_stale=False):
    qs = IntegratedArticleProcessingJob.objects.filter(status='queued').order_by('created_at')
    job_id = qs.values_list('id', flat=True).first()
    if job_id is None and recover_stale:
        job_id = (IntegratedArticleProcessingJob.objects.filter(
            status='running', updated_at__lt=timezone.now()-STALE_AFTER)
            .order_by('updated_at').values_list('id', flat=True).first())
    if job_id is None:
        return None
    return run_job(job_id, recover_stale=recover_stale)
