"""Pemulihan eksplisit untuk job gagal yang memiliki daftar kandidat tersimpan."""
from django.db import transaction
from apps.articles.models import Article

def tag_article_job(article_id, job_id):
    with transaction.atomic():
        article = Article.objects.select_for_update().get(pk=article_id)
        metadata = dict(article.raw_metadata or {})
        metadata["validation_process_job_id"] = job_id
        article.raw_metadata = metadata
        article.save(update_fields=["raw_metadata", "updated_at"])
