from dataclasses import dataclass, field
from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.indicators.models import Indicator
from apps.indicators.services import (
    indicator_is_eligible_for_signal,
)

from ..models import (
    IntelligenceRequirement,
    RequirementIndicator,
)


@dataclass(frozen=True)
class RequirementMatchCandidate:
    requirement: IntelligenceRequirement
    relevance_score: float
    reasons: list[str]


@dataclass
class RequirementMatchingResult:
    indicator: Indicator
    matches_created: list[RequirementIndicator] = field(
        default_factory=list
    )
    matches_updated: list[RequirementIndicator] = field(
        default_factory=list
    )
    skipped_reason: str = ""


def requirement_is_active_on_date(
    requirement: IntelligenceRequirement,
    reference_date: date,
) -> bool:
    if (
        not requirement.is_active
        or requirement.status
        != IntelligenceRequirement.Status.ACTIVE
    ):
        return False

    if (
        requirement.valid_from
        and reference_date < requirement.valid_from
    ):
        return False

    if (
        requirement.valid_until
        and reference_date > requirement.valid_until
    ):
        return False

    return True


def location_matches_requirement(
    *,
    indicator_location,
    requirement_location,
    include_descendants: bool,
) -> bool:
    if indicator_location == requirement_location:
        return True

    if not include_descendants:
        return False

    current = indicator_location.parent

    while current:
        if current == requirement_location:
            return True

        current = current.parent

    return False


def calculate_requirement_match(
    *,
    indicator: Indicator,
    requirement: IntelligenceRequirement,
) -> RequirementMatchCandidate | None:
    score = 0.0
    reasons: list[str] = []

    disease_links = requirement.requirement_diseases.select_related(
        "disease"
    )

    location_links = requirement.requirement_locations.select_related(
        "location"
    )

    active_keywords = requirement.keywords.filter(
        is_active=True
    )

    disease_links_exist = disease_links.exists()
    location_links_exist = location_links.exists()

    if indicator.disease:
        matching_disease = disease_links.filter(
            disease=indicator.disease
        ).first()

        if matching_disease:
            disease_score = min(
                0.40 * matching_disease.priority_weight,
                0.40,
            )

            score += disease_score

            reasons.append(
                f"Penyakit sesuai: {indicator.disease.name}."
            )
        elif disease_links_exist:
            return None

    if indicator.location:
        matched_location_link = None

        for location_link in location_links:
            if location_matches_requirement(
                indicator_location=indicator.location,
                requirement_location=location_link.location,
                include_descendants=(
                    location_link.include_descendants
                ),
            ):
                matched_location_link = location_link
                break

        if matched_location_link:
            location_score = min(
                0.25
                * matched_location_link.priority_weight,
                0.25,
            )

            score += location_score

            reasons.append(
                "Lokasi sesuai: "
                f"{matched_location_link.location.name}."
            )
        elif location_links_exist:
            return None

    searchable_text = " ".join(
        [
            indicator.summary or "",
            indicator.indicator_type.name,
            indicator.indicator_type.description or "",
            indicator.disease.name
            if indicator.disease
            else "",
            indicator.location.name
            if indicator.location
            else "",
        ]
    ).lower()

    keyword_score = 0.0
    matched_keywords: list[str] = []

    for requirement_keyword in active_keywords:
        keyword = requirement_keyword.keyword.strip().lower()

        if keyword and keyword in searchable_text:
            keyword_score += min(
                0.05 * requirement_keyword.weight,
                0.15,
            )

            matched_keywords.append(
                requirement_keyword.keyword
            )

    keyword_score = min(keyword_score, 0.20)

    if keyword_score:
        score += keyword_score

        reasons.append(
            "Kata kunci sesuai: "
            + ", ".join(matched_keywords[:5])
            + "."
        )

    type_mapping = {
        IntelligenceRequirement.RequirementType.DISEASE_EVENT: {
            "case-reported",
            "case-increase",
            "new-occurrence",
            "death-reported",
        },
        IntelligenceRequirement.RequirementType.GEOGRAPHIC_SPREAD: {
            "geographic-spread",
        },
        IntelligenceRequirement.RequirementType.IMPACT: {
            "death-reported",
        },
        IntelligenceRequirement.RequirementType.RESPONSE: {
            "government-response",
        },
        IntelligenceRequirement.RequirementType.ANOMALY: {
            "new-occurrence",
        },
    }

    accepted_indicator_codes = type_mapping.get(
        requirement.requirement_type,
        set(),
    )

    if (
        indicator.indicator_type.code
        in accepted_indicator_codes
    ):
        type_score = (
            0.20
            if indicator.indicator_type.code == "case-reported"
            else 0.15
        )
        score += type_score

        reasons.append(
            "Jenis indikator sesuai dengan jenis kebutuhan intelijen."
        )

    score = min(score, 1.0)

    if score < 0.30:
        return None

    return RequirementMatchCandidate(
        requirement=requirement,
        relevance_score=score,
        reasons=reasons,
    )


def find_requirement_matches(
    indicator: Indicator,
) -> list[RequirementMatchCandidate]:
    reference_date = (
        indicator.event_date
        or timezone.localdate()
    )

    requirements = (
        IntelligenceRequirement.objects.filter(
            is_active=True
        )
        .prefetch_related(
            "requirement_diseases__disease",
            "requirement_locations__location",
            "keywords",
        )
    )

    matches: list[RequirementMatchCandidate] = []

    for requirement in requirements:
        if not requirement_is_active_on_date(
            requirement,
            reference_date,
        ):
            continue

        candidate = calculate_requirement_match(
            indicator=indicator,
            requirement=requirement,
        )

        if candidate:
            matches.append(candidate)

    return sorted(
        matches,
        key=lambda item: item.relevance_score,
        reverse=True,
    )


@transaction.atomic
def match_indicator_to_requirements(
    indicator: Indicator,
) -> RequirementMatchingResult:
    result = RequirementMatchingResult(
        indicator=indicator,
    )

    eligible, reason = (
        indicator_is_eligible_for_signal(
            indicator
        )
    )

    if not eligible:
        result.skipped_reason = reason
        return result

    candidates = find_requirement_matches(
        indicator
    )

    if not candidates:
        result.skipped_reason = (
            "Tidak ditemukan kebutuhan intelijen yang relevan."
        )
        return result

    for candidate in candidates:
        relation, created = (
            RequirementIndicator.objects.update_or_create(
                requirement=candidate.requirement,
                indicator=indicator,
                defaults={
                    "relevance_score": (
                        candidate.relevance_score
                    ),
                    "relevance_reason": " ".join(
                        candidate.reasons
                    ),
                    "matched_by": (
                        RequirementIndicator.MatchSource.SYSTEM
                    ),
                },
            )
        )

        if created:
            result.matches_created.append(
                relation
            )
        else:
            result.matches_updated.append(
                relation
            )

    return result
