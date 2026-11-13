"""Rekomendasi kategori AI pra-hitung untuk artikel yang belum diklasifikasi.

- Tidak berjalan saat GET dashboard (menghindari biaya/latensi per render).
- Aman untuk restart dan beberapa worker: klaim singkat memakai row lock.
- Tidak menyetujui klasifikasi dan tidak menyentuh keputusan validasi/bukti.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.articles.models import Article
from .ai_information_type import article_fingerprint, request_suggestion, suggestion_for
from .information_type_review import confirmed_information_type, classification_allowed

logger = logging.getLogger(__name__)
STATE_KEY = 'information_type_auto_generation'
STALE_AFTER = timedelta(minutes=15)


def eligible(article, *, retry_failed=False, now=None):
    if not classification_allowed(article):
        return False
    if confirmed_information_type(article) is not None or suggestion_for(article) is not None:
        return False
    md = article.raw_metadata or {}
    state = md.get(STATE_KEY) or {}
    if not isinstance(state, dict):
        state = {}
    fingerprint = article_fingerprint(article)
    if state.get('fingerprint') != fingerprint:
        return True  # isi berubah: saran lama memang tidak lagi berlaku
    if state.get('status') == 'failed' and not retry_failed:
        return False
    if state.get('status') == 'running':
        try:
            started = timezone.datetime.fromisoformat(state.get('started_at', ''))
            if timezone.is_naive(started):
                started = timezone.make_aware(started)
            if (now or timezone.now()) - started < STALE_AFTER:
                return False
        except (ValueError, TypeError, OverflowError):
            pass
    return True


def generate_one(article_id, *, retry_failed=False):
    """Kembalikan generated, skipped, atau failed. Tidak melempar error API."""
    now = timezone.now()
    with transaction.atomic():
        article = Article.objects.select_for_update().get(pk=article_id)
        if not eligible(article, retry_failed=retry_failed, now=now):
            return 'skipped'
        fingerprint = article_fingerprint(article)
        md = dict(article.raw_metadata or {})
        old = md.get(STATE_KEY) or {}
        attempts = old.get('attempts', 0) if isinstance(old, dict) and old.get('fingerprint') == fingerprint else 0
        md[STATE_KEY] = {'status': 'running', 'fingerprint': fingerprint,
                         'started_at': now.isoformat(), 'attempts': attempts + 1}
        article.raw_metadata = md
        article.save(update_fields=['raw_metadata', 'updated_at'])

    try:
        request_suggestion(article_id)
        outcome = 'generated'
        error_name = None
    except Exception as exc:
        outcome = 'failed'
        error_name = type(exc).__name__  # tidak simpan API key / isi respons API
        logger.warning('Rekomendasi kategori gagal artikel=%s tipe=%s', article_id, error_name)

    with transaction.atomic():
        current = Article.objects.select_for_update().get(pk=article_id)
        if article_fingerprint(current) == fingerprint:
            md = dict(current.raw_metadata or {})
            state = md.get(STATE_KEY) or {}
            # Jangan menimpa state baru yang diklaim worker lain.
            if isinstance(state, dict) and state.get('fingerprint') == fingerprint and state.get('started_at') == now.isoformat():
                state = dict(state)
                state.update(status='done' if outcome == 'generated' else 'failed',
                             finished_at=timezone.now().isoformat())
                if error_name:
                    state['error_type'] = error_name
                md[STATE_KEY] = state
                current.raw_metadata = md
                current.save(update_fields=['raw_metadata', 'updated_at'])
    return outcome


def generate_available(limit=5, *, retry_failed=False):
    """Ambil terbaru dulu supaya artikel baru tidak tertahan backlog historis."""
    results = {'generated': 0, 'failed': 0, 'skipped': 0}
    inspected = 0
    for article in Article.objects.exclude(processing_status=Article.ProcessingStatus.REJECTED).select_related('source', 'validation_assessment').order_by('-created_at').iterator(chunk_size=100):
        if sum(results.values()) >= limit:
            break
        inspected += 1
        if not eligible(article, retry_failed=retry_failed):
            continue
        result = generate_one(article.pk, retry_failed=retry_failed)
        results[result] += 1
    results['inspected'] = inspected
    return results
