from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.indicators.models import Indicator
from apps.indicators.services import (
    indicator_is_eligible_for_signal,
)
from apps.requirements.models import RequirementIndicator

from ..models import (
    Signal,
    SignalArticle,
    SignalDisease,
    SignalIndicator,
    SignalLocation,
    SignalRequirement,
)


SIGNAL_TIME_WINDOW_DAYS = 14


@dataclass
class SignalGenerationResult:
    indicator: Indicator
    signal: Signal | None = None
    created: bool = False
    indicator_added: bool = False
    articles_added: int = 0
    requirements_added: int = 0
    skipped_reason: str = ""


def generate_signal_code() -> str:
    year = timezone.localdate().year

    prefix = f"SIG-{year}-"

    latest_signal = (
        Signal.objects.filter(
            code__startswith=prefix
        )
        .order_by("-code")
        .first()
    )

    if latest_signal:
        try:
            latest_number = int(
                latest_signal.code.split("-")[-1]
            )
        except ValueError:
            latest_number = 0
    else:
        latest_number = 0

    return f"{prefix}{latest_number + 1:06d}"


def find_existing_signal(
    indicator: Indicator,
) -> Signal | None:
    if not indicator.event_date:
        return None

    start_date = (
        indicator.event_date
        - timedelta(days=SIGNAL_TIME_WINDOW_DAYS)
    )

    end_date = (
        indicator.event_date
        + timedelta(days=SIGNAL_TIME_WINDOW_DAYS)
    )

    return (
        Signal.objects.filter(
            primary_disease=indicator.disease,
            primary_location=indicator.location,
            event_start_date__range=(
                start_date,
                end_date,
            ),
        )
        .exclude(
            status__in=[
                Signal.Status.REJECTED,
                Signal.Status.CLOSED,
            ]
        )
        .order_by("-last_updated_at")
        .first()
    )


def build_signal_title(
    indicator: Indicator,
) -> str:
    return (
        f"Indikasi {indicator.indicator_type.name} "
        f"{indicator.disease.name} "
        f"di {indicator.location.name}"
    )


def build_signal_summary(
    indicator: Indicator,
) -> str:
    return (
        f"Terdeteksi indikator "
        f"{indicator.indicator_type.name.lower()} "
        f"terkait {indicator.disease.name} "
        f"di {indicator.location.name}. "
        f"Indikator awal: {indicator.summary}"
    )


def calculate_signal_system_score(
    signal: Signal,
) -> float:
    indicator_links = signal.signal_indicators.select_related(
        "indicator",
        "indicator__indicator_type",
    )

    if not indicator_links.exists():
        return 0.0

    total_score = 0.0
    total_weight = 0.0

    for relation in indicator_links:
        indicator = relation.indicator

        confidence = indicator.confidence_score or 0.5

        weight = (
            indicator.indicator_type.default_weight
            * relation.importance_score
        )

        total_score += confidence * weight
        total_weight += weight

    if total_weight == 0:
        return 0.0

    score = total_score / total_weight

    supporting_articles = signal.signal_articles.count()

    if supporting_articles > 1:
        score += min(
            0.05 * (supporting_articles - 1),
            0.15,
        )

    return min(score, 1.0)


def derive_initial_priority(
    signal: Signal,
) -> str:
    indicator_codes = set(
        signal.indicators.values_list(
            "indicator_type__code",
            flat=True,
        )
    )

    if "death-reported" in indicator_codes:
        return Signal.PriorityLevel.HIGH

    if "geographic-spread" in indicator_codes:
        return Signal.PriorityLevel.HIGH

    if "new-occurrence" in indicator_codes:
        return Signal.PriorityLevel.HIGH

    if "case-increase" in indicator_codes:
        return Signal.PriorityLevel.MEDIUM

    return Signal.PriorityLevel.LOW


def derive_initial_confidence(
    score: float,
) -> str:
    if score >= 0.80:
        return Signal.ConfidenceLevel.HIGH

    if score >= 0.60:
        return Signal.ConfidenceLevel.MEDIUM

    return Signal.ConfidenceLevel.LOW


@transaction.atomic
def generate_signal_from_indicator(
    indicator: Indicator,
) -> SignalGenerationResult:
    result = SignalGenerationResult(
        indicator=indicator,
    )

    eligible, reason = indicator_is_eligible_for_signal(
        indicator
    )

    if not eligible:
        result.skipped_reason = reason
        return result

    requirement_matches = (
        RequirementIndicator.objects.filter(
            indicator=indicator
        )
        .select_related("requirement")
        .order_by("-relevance_score")
    )

    if not requirement_matches.exists():
        result.skipped_reason = (
            "Indikator belum dikaitkan dengan kebutuhan intelijen."
        )
        return result

    signal = find_existing_signal(indicator)

    if signal is None:
        signal = Signal.objects.create(
            code=generate_signal_code(),
            title=build_signal_title(indicator),
            summary=build_signal_summary(indicator),
            primary_disease=indicator.disease,
            primary_location=indicator.location,
            event_start_date=indicator.event_date,
            event_end_date=indicator.event_date,
            status=Signal.Status.NEEDS_REVIEW,
            priority_level=Signal.PriorityLevel.MEDIUM,
            confidence_level=Signal.ConfidenceLevel.UNASSESSED,
            created_by_system=True,
        )

        SignalDisease.objects.create(
            signal=signal,
            disease=indicator.disease,
            is_primary=True,
        )

        SignalLocation.objects.create(
            signal=signal,
            location=indicator.location,
            is_primary=True,
        )

        result.created = True

    relation, indicator_added = (
        SignalIndicator.objects.get_or_create(
            signal=signal,
            indicator=indicator,
            defaults={
                "importance_score": (
                    indicator.indicator_type.default_weight
                ),
                "is_primary": not signal.signal_indicators.exists(),
            },
        )
    )

    result.indicator_added = indicator_added

    evidence_relations = indicator.evidences.select_related(
        "article"
    )

    for evidence in evidence_relations:
        _, article_created = SignalArticle.objects.get_or_create(
            signal=signal,
            article=evidence.article,
            defaults={
                "support_type": (
                    SignalArticle.SupportType.PRIMARY
                    if evidence.is_primary_evidence
                    else SignalArticle.SupportType.SUPPORTING
                ),
                "relevance_score": (
                    evidence.confidence_score
                ),
                "is_primary_source": (
                    evidence.is_primary_evidence
                ),
            },
        )

        if article_created:
            result.articles_added += 1

    for requirement_match in requirement_matches:
        _, requirement_created = (
            SignalRequirement.objects.update_or_create(
                signal=signal,
                requirement=requirement_match.requirement,
                defaults={
                    "relevance_score": (
                        requirement_match.relevance_score
                    ),
                    "relevance_reason": (
                        requirement_match.relevance_reason
                    ),
                    "is_primary": (
                        not signal.signal_requirements.exists()
                    ),
                },
            )
        )

        if requirement_created:
            result.requirements_added += 1

    indicator_dates = list(
        signal.indicators.exclude(
            event_date__isnull=True
        ).values_list(
            "event_date",
            flat=True,
        )
    )

    if indicator_dates:
        signal.event_start_date = min(indicator_dates)
        signal.event_end_date = max(indicator_dates)

    signal.system_score = calculate_signal_system_score(
        signal
    )

    signal.priority_level = derive_initial_priority(
        signal
    )

    signal.confidence_level = derive_initial_confidence(
        signal.system_score
    )

    signal.save(
        update_fields=[
            "event_start_date",
            "event_end_date",
            "system_score",
            "priority_level",
            "confidence_level",
            "updated_at",
            "last_updated_at",
        ]
    )

    result.signal = signal

    return result