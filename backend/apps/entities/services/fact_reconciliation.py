"""Conservative, read-only reconciliation for MEDINTEL numeric case facts.

No writes and no calls to OpenAI. Flags indicate review needs, not errors.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from apps.entities.models import ArticleDisease, ArticleFact, ValidationStatus
from .fact_extraction import distinct_case_counts_in_sentence

# A number in fact_text must be verifiable without accepting e.g. 88 in 188.
def numeric_literal_present(text: str, number: int | None) -> bool:
    if number is None:
        return True
    grouped = f"{number:,}".replace(",", ".")
    variants = {str(number), grouped}
    return any(
        re.search(r"(?<!\d)" + re.escape(candidate) + r"(?!\d)", text or "")
        for candidate in variants
    )


def reporting_period_hint(text: str) -> str:
    """Find a textual time period; NEVER infer a single event_date from it."""
    text = text or ""
    months = (
        "januari|februari|maret|april|mei|juni|juli|agustus|"
        "september|oktober|november|desember"
    )
    patterns = [
        rf"\b(?:{months})\s*(?:hingga|sampai|s\.?d\.?|[-–])\s*"
        rf"(?:{months})\s+\d{{4}}\b",
        rf"\b(?:hingga|sampai)\s+minggu\s+ke[- ]?\d+\s+tahun\s+\d{{4}}\b",
        r"\bminggu\s+ke[- ]?\d+\s+tahun\s+\d{4}\b",
        r"\b(?:tahun|sepanjang)\s+\d{4}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return ""


def conflicting_administrative_scopes(text: str) -> bool:
    """Flag specific two-level claims; does not attempt geo resolution."""
    if len(distinct_case_counts_in_sentence(text)) < 2:
        return False
    return bool(
        re.search(r"\bprovinsi\s+\w+", text, re.I)
        and re.search(r"\b(?:kota|kabupaten)\s+\w+", text, re.I)
    )


def suggested_numeric_quote(article_text: str, number: int | None) -> str:
    """Human review hint only; never replaces fact_text automatically."""
    if number is None or not article_text:
        return ""
    grouped = f"{number:,}".replace(",", ".")
    number_group = "(?:" + "|".join(map(re.escape, {str(number), grouped})) + ")"
    match = re.search(
        r"(?<!\d)" + number_group + r"(?!\d)\s+kasus\b",
        article_text, re.IGNORECASE,
    )
    if match is None:
        return ""
    from .fact_extraction import extract_evidence_sentence
    return extract_evidence_sentence(text=article_text, anchor_start=match.start())[:350]


@dataclass(frozen=True)
class ReconciliationFinding:
    fact_id: str
    article_id: str
    issues: tuple[str, ...]
    primary_disease_suggestion: str
    period_hint: str
    sibling_fact_ids: tuple[str, ...]


def assess_fact(fact: ArticleFact, *, siblings: Iterable[ArticleFact] | None = None) -> ReconciliationFinding:
    """Read-only triage; a finding is never an approval or a correction."""
    siblings = tuple(siblings) if siblings is not None else tuple(
        ArticleFact.objects.filter(article_id=fact.article_id)
    )
    diseases = list(
        ArticleDisease.objects.filter(article_id=fact.article_id)
        .exclude(validation_status=ValidationStatus.REJECTED)
        .select_related("disease")
    )
    primary = [r for r in diseases if r.is_primary]
    suggestion = diseases[0].disease.name if not primary and len(diseases) == 1 else ""
    issues: list[str] = []
    if not primary:
        issues.append("PRIMARY_DISEASE_UNSET")
    elif len(primary) > 1:
        issues.append("MULTIPLE_PRIMARY_DISEASES")
    if not numeric_literal_present(fact.fact_text, fact.case_count):
        issues.append("CASE_COUNT_NOT_IN_QUOTE")
    if not numeric_literal_present(fact.fact_text, fact.death_count):
        issues.append("DEATH_COUNT_NOT_IN_QUOTE")
    if fact.event_date is None:
        issues.append("EVENT_DATE_UNSET")
    if fact.validation_status == ValidationStatus.UNREVIEWED:
        issues.append("FACT_UNREVIEWED")
    if conflicting_administrative_scopes(fact.fact_text):
        issues.append("MULTIPLE_REGIONAL_SCOPES")
    others=[]
    for sibling in siblings:
        if sibling.pk == fact.pk:
            continue
        if (fact.case_count is None or fact.case_count != sibling.case_count
            or fact.disease_id != sibling.disease_id):
            continue
        if fact.event_date and sibling.event_date and fact.event_date != sibling.event_date:
            continue
        # Only flag overlapping location scope; two explicit different
        # locations can have the same count and are not duplicates by default.
        if (fact.location_id == sibling.location_id or
            fact.location_id is None or sibling.location_id is None):
            others.append(str(sibling.pk))
    if others:
        issues.append("POSSIBLE_DUPLICATE_REVIEW")
    return ReconciliationFinding(
        fact_id=str(fact.pk), article_id=str(fact.article_id),
        issues=tuple(issues), primary_disease_suggestion=suggestion,
        period_hint=reporting_period_hint(fact.article.content_text),
        sibling_fact_ids=tuple(sorted(others)),
    )
