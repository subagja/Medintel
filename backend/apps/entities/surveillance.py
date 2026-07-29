import re
from dataclasses import dataclass

from apps.entities.models import (
    Disease,
    SurveillanceDisease,
    SurveillanceProgram,
)


@dataclass(frozen=True)
class DiseaseTermMatch:
    disease_id: str
    disease_name: str
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class SurveillanceTextMatch:
    is_relevant: bool
    matches: tuple[DiseaseTermMatch, ...]
    reason: str


def normalize_surveillance_text(
    value: str,
) -> str:
    normalized = value.casefold()

    normalized = re.sub(
        r"[\W_]+",
        " ",
        normalized,
        flags=re.UNICODE,
    )

    normalized = re.sub(
        r"\s+",
        " ",
        normalized,
    )

    return normalized.strip()


def term_is_present(
    *,
    normalized_text: str,
    term: str,
) -> bool:
    normalized_term = normalize_surveillance_text(
        term
    )

    if not normalized_term:
        return False

    pattern = (
        r"(?<!\w)"
        + re.escape(normalized_term)
        + r"(?!\w)"
    )

    return (
        re.search(
            pattern,
            normalized_text,
            flags=re.IGNORECASE,
        )
        is not None
    )


def get_active_surveillance_diseases():
    return (
        Disease.objects.filter(
            is_active=True,
            surveillance_memberships__is_active=True,
            surveillance_memberships__program__status=(
                SurveillanceProgram.Status.ACTIVE
            ),
        )
        .distinct()
        .prefetch_related(
            "aliases",
        )
        .order_by(
            "name",
        )
    )


def match_text_to_surveillance(
    text: str,
) -> SurveillanceTextMatch:
    normalized_text = normalize_surveillance_text(
        text
    )

    if not normalized_text:
        return SurveillanceTextMatch(
            is_relevant=False,
            matches=(),
            reason="Teks artikel kosong.",
        )

    disease_matches: list[DiseaseTermMatch] = []

    for disease in get_active_surveillance_diseases():
        candidate_terms = {
            disease.name,
        }

        if disease.canonical_name:
            candidate_terms.add(
                disease.canonical_name
            )

        candidate_terms.update(
            disease.aliases.filter(
                is_active=True,
            ).values_list(
                "alias",
                flat=True,
            )
        )

        matched_terms = tuple(
            sorted(
                {
                    term
                    for term in candidate_terms
                    if term_is_present(
                        normalized_text=normalized_text,
                        term=term,
                    )
                },
                key=str.casefold,
            )
        )

        if not matched_terms:
            continue

        disease_matches.append(
            DiseaseTermMatch(
                disease_id=str(disease.id),
                disease_name=disease.name,
                matched_terms=matched_terms,
            )
        )

    if not disease_matches:
        return SurveillanceTextMatch(
            is_relevant=False,
            matches=(),
            reason=(
                "Tidak ditemukan nama atau alias penyakit "
                "yang termasuk program surveilans aktif."
            ),
        )

    return SurveillanceTextMatch(
        is_relevant=True,
        matches=tuple(disease_matches),
        reason=(
            "Ditemukan penyakit yang termasuk "
            "program surveilans aktif."
        ),
    )