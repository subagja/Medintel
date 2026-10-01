"""Antrean ekstraksi bergilir dan pelengkapan bukti tanpa keputusan validasi."""

from django.db import transaction
from django.db.models import CharField, Count, Exists, F, OuterRef, Q
from django.db.models.functions import Cast
from django.utils import timezone

from apps.articles.models import Article
from apps.entities.models import ArticleDisease, ArticleFact, ArticleLocation, ValidationStatus
from .article_data_review import reextract_article_data
from .ai_evidence import apply_ai_evidence
from .information_balance import recommend_information_balance


def evidence_queryset():
    """Kelengkapan memakai bukti numerik yang terhubung, seperti validasi bulk."""
    diseases = ArticleDisease.objects.filter(article_id=OuterRef("pk")).exclude(
        validation_status=ValidationStatus.REJECTED,
    )
    locations = ArticleLocation.objects.filter(
        article_id=OuterRef("pk"), is_primary=True,
    ).exclude(validation_status=ValidationStatus.REJECTED)
    numeric = ArticleFact.objects.filter(article_id=OuterRef("pk")).exclude(
        validation_status=ValidationStatus.REJECTED,
    ).filter(
        Q(case_count__isnull=False) | Q(death_count__isnull=False)
        | Q(recovery_count__isnull=False) | Q(hospitalized_count__isnull=False),
    )
    linked = numeric.annotate(
        disease_link=Exists(ArticleDisease.objects.filter(
            article_id=OuterRef("article_id"), disease_id=OuterRef("disease_id"),
        ).exclude(validation_status=ValidationStatus.REJECTED)),
        location_link=Exists(ArticleLocation.objects.filter(
            article_id=OuterRef("article_id"), location_id=OuterRef("location_id"),
            is_primary=True,
        ).exclude(validation_status=ValidationStatus.REJECTED)),
    ).filter(disease_link=True, location_link=True, fact_text__regex=r"\S")
    return Article.objects.annotate(
        extraction_has_disease=Exists(diseases),
        extraction_has_location=Exists(locations),
        extraction_has_numeric_fact=Exists(numeric),
        extraction_has_linked_fact=Exists(linked),
        extraction_primary_count=Count("article_locations", filter=Q(
            article_locations__is_primary=True,
        ) & ~Q(article_locations__validation_status=ValidationStatus.REJECTED), distinct=True),
    )


def incomplete_queryset():
    return evidence_queryset().exclude(
        processing_status=Article.ProcessingStatus.REJECTED,
    ).exclude(extraction_has_linked_fact=True, extraction_primary_count=1)


def select_batch_ids(limit, force=False):
    qs = evidence_queryset().exclude(processing_status=Article.ProcessingStatus.REJECTED)
    if not force:
        qs = incomplete_queryset()
    return list(qs.annotate(
        extraction_attempt=Cast("raw_metadata__extraction_review_attempt_at", CharField()),
    ).order_by(F("extraction_attempt").asc(nulls_first=True), "created_at", "pk")
        .values_list("pk", flat=True)[:limit])


def evidence_snapshot(article_id):
    row = evidence_queryset().get(pk=article_id)
    complete = row.extraction_has_linked_fact and row.extraction_primary_count == 1
    missing = []
    if not row.extraction_has_disease:
        missing.append("penyakit")
    if not row.extraction_has_location:
        missing.append("lokasi utama")
    if row.extraction_primary_count > 1:
        missing.append("lokasi utama ambigu")
    if not row.extraction_has_numeric_fact:
        missing.append("angka kasus/kematian/pasien")
    if not complete and not missing:
        missing.append("bukti numerik yang terhubung ke penyakit dan lokasi")
    # Bandingkan isi, sehingga perbaikan relasi terdeteksi meski jumlahnya sama.
    fingerprint = (
        tuple(ArticleDisease.objects.filter(article_id=article_id).order_by("pk").values_list(
            "pk", "disease_id", "is_primary", "validation_status")),
        tuple(ArticleLocation.objects.filter(article_id=article_id).order_by("pk").values_list(
            "pk", "location_id", "is_primary", "validation_status")),
        tuple(ArticleFact.objects.filter(article_id=article_id).order_by("pk").values_list(
            "pk", "disease_id", "location_id", "case_count", "death_count",
            "recovery_count", "hospitalized_count", "fact_text", "validation_status")),
    )
    return {"complete": complete, "missing": missing, "fingerprint": fingerprint}


def review_for_extraction(article_id, with_ai=False, force=False):
    before = evidence_snapshot(article_id)
    # Catat percobaan sebelum bekerja: kegagalan juga tidak menahan giliran.
    with transaction.atomic():
        article = Article.objects.select_for_update().select_related("source").get(pk=article_id)
        if article.processing_status == Article.ProcessingStatus.REJECTED:
            return {"skipped": True, "changed": False, "became_complete": False,
                    "complete": before["complete"], "missing": before["missing"], "ai_failed": False}
        metadata = dict(article.raw_metadata or {})
        metadata["extraction_review_attempt_at"] = timezone.now().isoformat()
        article.raw_metadata = metadata
        article.save(update_fields=["raw_metadata", "updated_at"])
        complete = reextract_article_data(article, force=force)

    ai_failed = False
    if with_ai and not complete:
        # Panggilan jaringan di luar transaksi DB. Rekomendasi validasi diabaikan.
        from apps.assessments.management.commands.bulk_validate_articles import Command
        try:
            ai_result = Command()._call_ai_assessment(article, recommend_information_balance(article))
            with transaction.atomic():
                current = Article.objects.select_for_update().get(pk=article_id)
                if current.processing_status != Article.ProcessingStatus.REJECTED:
                    apply_ai_evidence(current, ai_result.get("evidence"))
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Pelengkapan AI gagal artikel=%s", article_id)
            ai_failed = True

    after = evidence_snapshot(article_id)
    result = {"skipped": False, "changed": before["fingerprint"] != after["fingerprint"],
              "became_complete": not before["complete"] and after["complete"],
              "complete": after["complete"], "missing": after["missing"], "ai_failed": ai_failed}
    with transaction.atomic():
        current = Article.objects.select_for_update().get(pk=article_id)
        metadata = dict(current.raw_metadata or {})
        metadata["extraction_review_result"] = result
        current.raw_metadata = metadata
        fields = ["raw_metadata", "updated_at"]
        if current.processing_status not in (Article.ProcessingStatus.VALIDATED, Article.ProcessingStatus.REJECTED):
            current.processing_status = Article.ProcessingStatus.PROCESSED
            fields.append("processing_status")
        current.save(update_fields=fields)
    return result
