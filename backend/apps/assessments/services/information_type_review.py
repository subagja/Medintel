"""Manual classification of OSINT article type, independent of validation."""
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from apps.articles.models import Article

ALLOWED_CATEGORIES = frozenset({"case_event", "risk_indicator", "prevention_response", "uncertain"})
METADATA_KEY = "information_type_review"


def classification_allowed(article):
    """A pipeline 'processed' status is not an article validation decision.

    Pending articles can receive a *provisional* category recommendation, but
    a rejected validation decision always blocks classification/approval.
    """
    if article.processing_status == Article.ProcessingStatus.REJECTED:
        return False
    try:
        assessment = article.validation_assessment
    except ObjectDoesNotExist:
        return True
    return assessment.validation_status != 'rejected'


def confirmed_information_type(article):
    metadata = article.raw_metadata or {}
    review = metadata.get(METADATA_KEY)
    if not isinstance(review, dict):
        return None
    value = review.get("category")
    return value if value in ALLOWED_CATEGORIES else None


def confirm_information_type(article_id, category, reason, user):
    if category not in ALLOWED_CATEGORIES:
        raise ValueError("Kategori tidak dikenali")
    reason = (reason or "").strip()
    if len(reason) < 10 or len(reason) > 1000:
        raise ValueError("Alasan pemeriksaan wajib 10–1000 karakter")
    with transaction.atomic():
        article = Article.objects.select_for_update().get(pk=article_id)
        if not classification_allowed(article):
            raise ValueError("Artikel dengan keputusan validasi ditolak tidak boleh diklasifikasi")
        metadata = dict(article.raw_metadata or {})
        history = list(metadata.get("information_type_review_history") or [])
        prior = metadata.get(METADATA_KEY)
        if isinstance(prior, dict):
            history.append(prior)
        metadata["information_type_review_history"] = history[-50:]
        metadata[METADATA_KEY] = {
            "category": category,
            "reason": reason,
            "reviewed_at": timezone.now().isoformat(),
            "reviewed_by_id": user.pk,
            "reviewed_by": user.get_username(),
            "source": "manual_analyst",
        }
        article.raw_metadata = metadata
        article.save(update_fields=["raw_metadata", "updated_at"])
    return metadata[METADATA_KEY]
