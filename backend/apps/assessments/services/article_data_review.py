"""Review ulang bukti artikel tanpa mengubah keputusan validasi analis."""

from django.db import transaction

from apps.articles.models import Article
from apps.entities.models import ArticleDisease, ValidationStatus
from apps.entities.services import (
    extract_article_entities,
    extract_article_facts,
    extract_article_locations,
)

from .information_balance import has_complete_structured_evidence


@transaction.atomic
def reextract_article_data(article: Article, *, force: bool = False) -> bool:
    """Tambahkan bukti dari isi artikel, menjaga keputusan entitas analis.

    Return True jika bukti terstruktur menjadi lengkap. Tidak mengubah
    processing_status, assessment, atau entitas yang telah ditinjau analis.
    """
    if not force and has_complete_structured_evidence(article):
        return True

    reviewed_disease_exists = ArticleDisease.objects.filter(
        article=article,
    ).exclude(validation_status=ValidationStatus.UNREVIEWED).exists()

    if reviewed_disease_exists:
        extract_article_locations(article)
    else:
        extract_article_entities(article)
    extract_article_facts(article)
    return has_complete_structured_evidence(article)
