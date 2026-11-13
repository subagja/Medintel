"""Isolated dashboard endpoints; all actions are superuser-only."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone
from apps.assessments.models import IntegratedArticleProcessingJob
from apps.articles.models import Article
from apps.assessments.services.integrated_processing import enqueue_job, STALE_AFTER


def _superuser(request):
    if not request.user.is_superuser:
        raise PermissionDenied('Fitur batch terpadu hanya tersedia untuk Master Admin.')


@login_required
@require_POST
def start(request):
    _superuser(request)
    try:
        job = enqueue_job(
            request.user,
            limit=int(request.POST.get('limit', '5')),
            mode=request.POST.get('mode', 'new'),
            auto_approve=True,  # Superuser always authorizes AI category persistence
            ai_case_evidence=request.POST.get('ai_case_evidence') == '1',
        )
    except (ValueError, TypeError) as exc:
        messages.error(request, str(exc))
        return redirect('dashboard:entity-extraction')
    messages.success(request, 'Batch terpadu dibuat. Pastikan worker pengolahan terpadu aktif.')
    return redirect(reverse('dashboard:entity-extraction') + '?integrated_job=' + str(job.pk))


@login_required
@require_GET
def status(request):
    _superuser(request)
    job_id = request.GET.get('job', '').strip()
    qs = IntegratedArticleProcessingJob.objects.select_related('requested_by').order_by('-created_at')
    if job_id:
        try:
            job = qs.get(pk=job_id)
        except (ValueError, IntegratedArticleProcessingJob.DoesNotExist):
            return JsonResponse({'job': None}, status=404)
    else:
        job = qs.first()
    if not job:
        return JsonResponse({'job': None})
    stale = job.status in ('queued','running') and job.updated_at < timezone.now()-STALE_AFTER
    # Up to 50 persisted results are enriched with their source titles and stable
    # article links. This is read-only, restricted to an authenticated superuser.
    items = (job.results[-50:] if isinstance(job.results, list) else [])
    ids = [item.get('article_id') for item in items if isinstance(item, dict) and item.get('article_id')]
    # IDs are written by the worker from Article.pk; do not trust any client IDs.
    titles = {str(article_id): title for article_id, title in Article.objects.filter(
        pk__in=ids
    ).values_list('pk', 'title')} if ids else {}
    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result = dict(item)
        article_id = str(result.get('article_id') or '')
        result['title'] = titles.get(article_id, '')
        result['detail_url'] = (reverse('dashboard:article-extraction-detail',
                                      args=[article_id]) if article_id in titles else '')
        results.append(result)
    return JsonResponse({'job': {
        'id': str(job.pk), 'status': job.status, 'total': job.total_items,
        'processed': job.processed_items, 'stats': job.stats,
        'results': results,
        'error_message': job.error_message, 'stale': stale,
        'updated_at': job.updated_at.isoformat(),
        'auto_approve': job.auto_approve,
    }})
