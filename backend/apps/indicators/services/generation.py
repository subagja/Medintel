from dataclasses import dataclass, field

from django.db import transaction

from apps.entities.models import (
    ArticleFact,
    ValidationStatus,
)

from ..models import (
    Indicator,
    IndicatorEvidence,
    IndicatorType,
)


@dataclass
class IndicatorGenerationResult:
    article_fact: ArticleFact
    indicators_created: list[Indicator] = field(
        default_factory=list
    )
    indicators_existing: list[Indicator] = field(
        default_factory=list
    )
    skipped_reason: str = ""


def fact_is_eligible(
    fact: ArticleFact,
    *,
    disease=None,
    location=None,
) -> tuple[bool, str]:
    allowed_statuses = {
        ValidationStatus.VALIDATED,
        ValidationStatus.CORRECTED,
    }

    if fact.validation_status not in allowed_statuses:
        return (
            False,
            "Fakta belum divalidasi atau dikoreksi oleh analis.",
        )

    if not (disease or fact.disease):
        return (
            False,
            "Fakta tidak memiliki penyakit.",
        )

    if not (location or fact.location):
        return (
            False,
            "Fakta tidak memiliki lokasi.",
        )

    return True, ""


def build_indicator_candidates(
    fact: ArticleFact,
    *,
    disease=None,
    location=None,
) -> list[dict]:
    candidates: list[dict] = []
    disease = disease or fact.disease
    location = location or fact.location

    # Fakta numerik yang sudah divalidasi tetap merupakan indikator
    # pelaporan, meskipun artikel tidak menyatakan tren meningkat.
    # Statusnya masih perlu ditinjau analis dan tidak menyatakan KLB.
    if fact.case_count is not None:
        candidates.append(
            {
                "indicator_type_code": "case-reported",
                "value": fact.case_count,
                "unit": "kasus",
                "direction": Indicator.Direction.UNKNOWN,
                "summary": (
                    f"Dilaporkan {fact.case_count:,} kasus "
                    f"{disease.name} di {location.name}."
                ).replace(",", "."),
            }
        )

    # Jangan menebak ulang tren dari substring fact_text. Kata seperti
    # "meningkatkan surveilans" bukan peningkatan kasus. Arah indikator
    # hanya mengikuti tren fakta yang telah diklasifikasikan dan ditinjau.
    if fact.trend == ArticleFact.Trend.INCREASING:
        candidates.append(
            {
                "indicator_type_code": "case-increase",
                "value": fact.case_count,
                "unit": "kasus" if fact.case_count else "",
                "direction": Indicator.Direction.INCREASING,
                "summary": (
                    f"Terindikasi peningkatan kasus "
                    f"{disease.name} di {location.name}."
                ),
            }
        )

    if fact.death_count is not None and fact.death_count > 0:
        candidates.append(
            {
                "indicator_type_code": "death-reported",
                "value": fact.death_count,
                "unit": "kematian",
                "direction": Indicator.Direction.UNKNOWN,
                "summary": (
                    f"Dilaporkan {fact.death_count} kematian "
                    f"terkait {disease.name} "
                    f"di {location.name}."
                ),
            }
        )

    if fact.trend == ArticleFact.Trend.NEW_OCCURRENCE:
        candidates.append(
            {
                "indicator_type_code": "new-occurrence",
                "value": fact.case_count,
                "unit": "kasus" if fact.case_count else "",
                "direction": Indicator.Direction.NEW,
                "summary": (
                    f"Terindikasi kejadian baru "
                    f"{disease.name} di {location.name}."
                ),
            }
        )

    if fact.trend == ArticleFact.Trend.SPREADING:
        candidates.append(
            {
                "indicator_type_code": "geographic-spread",
                "value": None,
                "unit": "",
                "direction": Indicator.Direction.SPREADING,
                "summary": (
                    f"Terindikasi penyebaran "
                    f"{disease.name} di {location.name}."
                ),
            }
        )

    if fact.government_response.strip():
        candidates.append(
            {
                "indicator_type_code": "government-response",
                "value": None,
                "unit": "",
                "direction": Indicator.Direction.UNKNOWN,
                "summary": (
                    f"Terdapat respons pemerintah terhadap "
                    f"{disease.name} di {location.name}."
                ),
            }
        )

    return candidates


def calculate_indicator_confidence(
    fact: ArticleFact,
    indicator_type_code: str,
) -> float:
    score = fact.confidence_score or 0.50

    if fact.validation_status == ValidationStatus.VALIDATED:
        score += 0.10

    if fact.validation_status == ValidationStatus.CORRECTED:
        score += 0.05

    if indicator_type_code == "death-reported":
        if fact.death_count is not None:
            score += 0.10

    if indicator_type_code == "case-increase":
        if fact.case_count is not None:
            score += 0.05

    if indicator_type_code == "case-reported":
        if fact.case_count is not None:
            score += 0.05

    return min(score, 1.0)


@transaction.atomic
def generate_indicators_from_fact(
    fact: ArticleFact,
    *,
    disease=None,
    location=None,
) -> IndicatorGenerationResult:
    result = IndicatorGenerationResult(
        article_fact=fact,
    )

    effective_disease = disease or fact.disease
    effective_location = location or fact.location

    is_eligible, reason = fact_is_eligible(
        fact,
        disease=effective_disease,
        location=effective_location,
    )

    if not is_eligible:
        result.skipped_reason = reason
        return result

    candidates = build_indicator_candidates(
        fact,
        disease=effective_disease,
        location=effective_location,
    )

    if not candidates:
        result.skipped_reason = (
            "Fakta tidak memenuhi aturan pembentukan indikator."
        )
        return result

    for candidate in candidates:
        try:
            indicator_type = IndicatorType.objects.get(
                code=candidate["indicator_type_code"],
                is_active=True,
            )
        except IndicatorType.DoesNotExist:
            continue

        existing_indicator = (
            Indicator.objects.filter(
                indicator_type=indicator_type,
                disease=effective_disease,
                location=effective_location,
                event_date=fact.event_date,
                value=candidate["value"],
                direction=candidate["direction"],
            )
            .first()
        )

        if existing_indicator:
            IndicatorEvidence.objects.get_or_create(
                indicator=existing_indicator,
                article=fact.article,
                article_fact=fact,
                defaults={
                    "evidence_text": fact.fact_text,
                    "confidence_score": (
                        fact.confidence_score
                    ),
                    "is_primary_evidence": False,
                },
            )

            result.indicators_existing.append(
                existing_indicator
            )
            continue

        confidence = calculate_indicator_confidence(
            fact=fact,
            indicator_type_code=(
                candidate["indicator_type_code"]
            ),
        )

        indicator = Indicator.objects.create(
            indicator_type=indicator_type,
            disease=effective_disease,
            location=effective_location,
            event_date=fact.event_date,
            value=candidate["value"],
            unit=candidate["unit"],
            direction=candidate["direction"],
            summary=candidate["summary"],
            confidence_score=confidence,
            status=Indicator.Status.NEEDS_REVIEW,
            created_by_system=True,
        )

        IndicatorEvidence.objects.create(
            indicator=indicator,
            article=fact.article,
            article_fact=fact,
            evidence_text=fact.fact_text,
            confidence_score=fact.confidence_score,
            is_primary_evidence=True,
        )

        result.indicators_created.append(indicator)

    return result
