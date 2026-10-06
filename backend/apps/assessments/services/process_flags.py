"""Flags eksekusi terpisah dari status hasil; disimpan di metadata artikel."""
from django.db import transaction
from django.utils import timezone
from apps.articles.models import Article
from apps.assessments.models import ArticleValidationAssessment

LABELS = {"new": "Belum dinilai", "running": "Sedang dinilai", "done": "Sudah dinilai", "failed": "Gagal / terputus"}

def validation_state(article, assessment=None):
    if assessment is None:
        try: assessment = article.validation_assessment
        except ArticleValidationAssessment.DoesNotExist: pass
    metadata = article.raw_metadata or {}
    flag = metadata.get("validation_process_state")
    # Keputusan analis/final selalu lebih kuat dari flag otomatis.
    if assessment and (assessment.evaluated_by_id or assessment.validation_status != "pending"):
        return "done"
    if flag in ("running", "failed"):
        if flag == "running":
            from datetime import datetime, timedelta
            try:
                if timezone.now() - datetime.fromisoformat(metadata.get("validation_process_started_at", "")) > timedelta(hours=2):
                    return "failed"
            except (ValueError, TypeError): return "failed"
        return flag
    if flag == "done" or (assessment and assessment.auto_assessed_at): return "done"
    return "new"

def validation_candidates(mode="new"):
    # Sumber yang sama dipakai UI dan command. Final analis tidak diproses ulang.
    qs = Article.objects.filter(processing_status=Article.ProcessingStatus.PROCESSED).select_related("source", "validation_assessment").order_by("crawled_at", "pk")
    result = []
    for article in qs:
        try: assessment = article.validation_assessment
        except ArticleValidationAssessment.DoesNotExist: assessment = None
        if assessment and (assessment.validation_status != "pending" or assessment.evaluated_by_id): continue
        if stage_busy(article, "extraction_review"): continue
        state = validation_state(article, assessment)
        if state == mode: result.append((article, assessment))
    return result

def validation_counts():
    return {mode: len(validation_candidates(mode)) for mode in ("new", "done", "failed", "running")}

def mark_validation(article_id, state):
    with transaction.atomic():
        article = Article.objects.select_for_update().get(pk=article_id)
        metadata = dict(article.raw_metadata or {})
        metadata["validation_process_state"] = state
        metadata["validation_process_" + ("started_at" if state == "running" else "finished_at")] = timezone.now().isoformat()
        article.raw_metadata = metadata
        article.save(update_fields=["raw_metadata", "updated_at"])

def claim_validation(article_id, mode):
    with transaction.atomic():
        article = Article.objects.select_for_update().get(pk=article_id)
        if article.processing_status != Article.ProcessingStatus.PROCESSED: return False
        if stage_busy(article, "extraction_review"): return False
        try: assessment = article.validation_assessment
        except ArticleValidationAssessment.DoesNotExist: assessment = None
        if assessment and (assessment.validation_status != "pending" or assessment.evaluated_by_id): return False
        if validation_state(article, assessment) != mode: return False
        mark_validation(article_id, "running")
        return True


def stage_busy(article, prefix):
    metadata = article.raw_metadata or {}
    if metadata.get(prefix + "_state") != "running": return False
    from datetime import datetime, timedelta
    key = "extraction_review_attempt_at" if prefix == "extraction_review" else "validation_process_started_at"
    try:
        return timezone.now() - datetime.fromisoformat(metadata.get(key, "")) <= timedelta(hours=2)
    except (ValueError, TypeError):
        # Riwayat tidak jelas: jangan menjalankan duplikat.
        return True
