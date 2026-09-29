"""Simpan bukti AI hanya setelah diverifikasi terhadap teks dan data master."""

import re

from django.db import transaction
from django.db.models import Q

from apps.articles.models import Article
from apps.entities.eligibility import extract_count_mentions
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ExtractionMethod,
    ValidationStatus,
)
from apps.locations.models import Location


def _appears_in_quote(quote: str, terms) -> bool:
    return any(
        re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)",
                  quote.casefold())
        for term in terms if term
    )


def _unique_disease(name: str, quote: str):
    matches = Disease.objects.filter(
        Q(name__iexact=name)
        | Q(canonical_name__iexact=name)
        | Q(aliases__alias__iexact=name, aliases__is_active=True),
        is_active=True,
    ).distinct()
    if matches.count() != 1:
        return None
    disease = matches.first()
    terms = [disease.name, disease.canonical_name]
    terms += list(disease.aliases.filter(is_active=True).values_list("alias", flat=True))
    return disease if _appears_in_quote(quote, terms) else None


def _unique_location(name: str, quote: str):
    matches = Location.objects.filter(
        Q(name__iexact=name)
        | Q(aliases__alias__iexact=name, aliases__is_active=True),
        is_active=True,
    ).distinct()
    if matches.count() != 1:
        return None
    location = matches.first()
    terms = [location.name]
    terms += list(location.aliases.filter(is_active=True).values_list("alias", flat=True))
    return location if _appears_in_quote(quote, terms) else None


@transaction.atomic
def apply_ai_evidence(article: Article, evidence: dict | None) -> bool:
    """Tolak seluruh usulan jika salah satu unsur tidak terbukti secara literal."""
    if not evidence or not isinstance(evidence, dict):
        return False
    quote = str(evidence.get("evidence_quote") or "").strip()
    name_d = str(evidence.get("disease_name") or "").strip()
    name_l = str(evidence.get("location_name") or "").strip()
    full_text = f"{article.title}. {article.content_text or ''}"
    if not quote or len(quote) > 900 or quote.casefold() not in full_text.casefold():
        return False
    disease = _unique_disease(name_d, quote)
    location = _unique_location(name_l, quote)
    if disease is None or location is None:
        return False

    counts = extract_count_mentions(quote)
    case = evidence.get("case_count")
    death = evidence.get("death_count")
    if case is None and death is None:
        return False
    if case is not None and (
        not isinstance(case, int) or isinstance(case, bool) or case < 0
        or not any(m.value == case and m.metric_type in {
            "confirmed_case", "patient", "affected_person",
        } for m in counts)
    ):
        return False
    if death is not None and (
        not isinstance(death, int) or isinstance(death, bool) or death < 0
        or not any(m.value == death and m.metric_type == "death" for m in counts)
    ):
        return False

    # Jangan menimpa atau menghidupkan kembali keputusan analis.
    if ArticleDisease.objects.filter(article=article).exclude(
        validation_status=ValidationStatus.UNREVIEWED,
    ).exclude(disease=disease).exists():
        return False
    if ArticleDisease.objects.filter(
        article=article, disease=disease, validation_status=ValidationStatus.REJECTED,
    ).exists():
        return False
    if ArticleLocation.objects.filter(article=article).exclude(
        validation_status=ValidationStatus.UNREVIEWED,
    ).exclude(location=location).exists():
        return False
    if ArticleLocation.objects.filter(
        article=article, location=location, validation_status=ValidationStatus.REJECTED,
    ).exists():
        return False
    if ArticleLocation.objects.filter(
        article=article, is_primary=True,
    ).exclude(validation_status=ValidationStatus.REJECTED).exclude(
        location=location,
    ).exists():
        return False
    if ArticleFact.objects.filter(
        article=article, disease=disease, location=location,
        case_count=case, death_count=death,
        validation_status=ValidationStatus.REJECTED,
    ).exists():
        return False

    ArticleDisease.objects.get_or_create(
        article=article, disease=disease,
        defaults={"mention_text": name_d, "is_primary": True,
                  "extraction_method": ExtractionMethod.MODEL},
    )
    ArticleLocation.objects.get_or_create(
        article=article, location=location,
        defaults={"mention_text": name_l, "is_primary": True,
                  "extraction_method": ExtractionMethod.MODEL},
    )
    if not ArticleFact.objects.filter(
        article=article, disease=disease, location=location,
        case_count=case, death_count=death,
    ).exists():
        ArticleFact.objects.create(
            article=article, disease=disease, location=location,
            case_count=case, death_count=death, fact_text=quote,
            extraction_method=ExtractionMethod.MODEL,
        )
    return True
