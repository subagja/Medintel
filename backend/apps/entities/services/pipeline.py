from dataclasses import dataclass

from django.db import transaction

from apps.articles.models import Article

from ..models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
)
from .fact_extraction import (
    FactExtractionResult,
    extract_article_facts,
)
from .rule_based import (
    EntityExtractionResult,
    extract_article_entities,
)


@dataclass(frozen=True)
class ArticleEligibility:
    is_eligible: bool
    has_disease: bool
    has_location: bool
    has_numeric_fact: bool
    reason: str


def evaluate_article_eligibility(
    article: Article,
) -> ArticleEligibility:
    """Nilai kelengkapan data hasil ekstraksi satu artikel (dipakai
    sama-sama oleh command CLI `process_articles` dan halaman web
    "Ekstraksi Entitas", supaya definisi "lengkap"-nya konsisten).
    """
    has_disease = ArticleDisease.objects.filter(
        article=article,
    ).exists()

    has_location = ArticleLocation.objects.filter(
        article=article,
        location__country_code="ID",
    ).exists()

    facts = ArticleFact.objects.filter(
        article=article,
    )

    has_numeric_fact = (
        facts.filter(case_count__isnull=False).exists()
        or facts.filter(death_count__isnull=False).exists()
    )

    missing: list[str] = []

    if not has_disease:
        missing.append("penyakit")

    if not has_location:
        missing.append("lokasi")

    if not has_numeric_fact:
        missing.append("jumlah kasus/kematian")

    if missing:
        return ArticleEligibility(
            is_eligible=False,
            has_disease=has_disease,
            has_location=has_location,
            has_numeric_fact=has_numeric_fact,
            reason=(
                "Data ekstraksi belum lengkap: "
                + ", ".join(missing)
                + "."
            ),
        )

    return ArticleEligibility(
        is_eligible=True,
        has_disease=has_disease,
        has_location=has_location,
        has_numeric_fact=has_numeric_fact,
        reason="Data ekstraksi lengkap.",
    )


@dataclass
class FullProcessingResult:
    article: Article
    entity_result: EntityExtractionResult
    fact_result: FactExtractionResult
    eligibility: ArticleEligibility
    disease_count: int
    location_count: int
    fact_count: int


@transaction.atomic
def process_article_full(
    article: Article,
) -> FullProcessingResult:
    """Jalankan ekstraksi entitas + fakta LENGKAP untuk satu artikel,
    plus perbarui processing_status dan raw_metadata -- versi lengkap
    dari `exploit_article()` yang dipakai command `process_articles`
    dan halaman web "Ekstraksi Entitas" (sama persis, satu sumber
    logic, supaya hasilnya konsisten dari manapun dipicu).
    """
    from django.utils import timezone

    article.processing_status = (
        Article.ProcessingStatus.PROCESSING
    )
    article.rejection_reason = ""
    article.save(
        update_fields=[
            "processing_status",
            "rejection_reason",
            "updated_at",
        ]
    )

    entity_result = extract_article_entities(article)
    fact_result = extract_article_facts(article)
    eligibility = evaluate_article_eligibility(article)

    pipeline_metadata = {
        **(article.raw_metadata or {}),
        "geolocation": entity_result.geolocation_metadata(),
        "processing_pipeline": {
            "processed_at": timezone.now().isoformat(),
            "eligibility": (
                "eligible" if eligibility.is_eligible else "needs_review"
            ),
            "reason": eligibility.reason,
            "has_disease": eligibility.has_disease,
            "has_location": eligibility.has_location,
            "geographic_scope": entity_result.geolocation_scope,
            "has_numeric_fact": eligibility.has_numeric_fact,
        },
    }

    article.processing_status = (
        Article.ProcessingStatus.PROCESSED
    )
    article.raw_metadata = pipeline_metadata
    article.rejection_reason = ""
    article.save(
        update_fields=[
            "processing_status",
            "raw_metadata",
            "rejection_reason",
            "updated_at",
        ]
    )

    return FullProcessingResult(
        article=article,
        entity_result=entity_result,
        fact_result=fact_result,
        eligibility=eligibility,
        disease_count=ArticleDisease.objects.filter(
            article=article,
        ).count(),
        location_count=ArticleLocation.objects.filter(
            article=article,
        ).count(),
        fact_count=ArticleFact.objects.filter(
            article=article,
        ).count(),
    )


@dataclass(frozen=True)
class ArticleExploitationResult:
    article: Article
    entity_result: EntityExtractionResult
    fact_result: FactExtractionResult


@transaction.atomic
def exploit_article(
    article: Article,
) -> ArticleExploitationResult:
    entity_result = extract_article_entities(
        article
    )

    fact_result = extract_article_facts(
        article
    )

    article.processing_status = (
        Article.ProcessingStatus.PROCESSED
    )

    article.save(
        update_fields=[
            "processing_status",
            "updated_at",
        ]
    )

    return ArticleExploitationResult(
        article=article,
        entity_result=entity_result,
        fact_result=fact_result,
    )