from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.articles.models import Article
from apps.signals.models import Signal
from apps.sources.models import Source

from ..models import (
    AssessmentLevel,
    InformationEvaluation,
    SourceEvaluation,
)


@dataclass(frozen=True)
class EvaluationAggregate:
    score: float
    count: int


def score_to_level(
    score: float,
) -> str:
    if score >= 0.85:
        return AssessmentLevel.VERY_HIGH

    if score >= 0.70:
        return AssessmentLevel.HIGH

    if score >= 0.50:
        return AssessmentLevel.MEDIUM

    return AssessmentLevel.LOW


def calculate_source_reliability(
    *,
    historical_accuracy_score: float,
    authority_score: float,
    transparency_score: float,
    independence_score: float,
) -> float:
    score = (
        historical_accuracy_score * 0.30
        + authority_score * 0.30
        + transparency_score * 0.25
        + independence_score * 0.15
    )

    return round(score, 4)


def calculate_information_credibility(
    *,
    corroboration_score: float,
    consistency_score: float,
    specificity_score: float,
    timeliness_score: float,
) -> float:
    score = (
        corroboration_score * 0.30
        + consistency_score * 0.30
        + specificity_score * 0.25
        + timeliness_score * 0.15
    )

    return round(score, 4)


def ensure_source_supports_signal(
    *,
    signal: Signal,
    source: Source,
) -> None:
    exists = signal.signal_articles.filter(
        article__source=source,
    ).exists()

    if not exists:
        raise ValidationError(
            "Sumber tidak memiliki artikel pendukung pada sinyal ini."
        )


def ensure_article_supports_signal(
    *,
    signal: Signal,
    article: Article,
) -> None:
    exists = signal.signal_articles.filter(
        article=article,
    ).exists()

    if not exists:
        raise ValidationError(
            "Artikel bukan bagian dari bukti pendukung sinyal."
        )


@transaction.atomic
def evaluate_source(
    *,
    signal: Signal,
    source: Source,
    evaluator,
    historical_accuracy_score: float,
    authority_score: float,
    transparency_score: float,
    independence_score: float,
    notes: str = "",
) -> SourceEvaluation:
    ensure_source_supports_signal(
        signal=signal,
        source=source,
    )

    reliability_score = calculate_source_reliability(
        historical_accuracy_score=(
            historical_accuracy_score
        ),
        authority_score=authority_score,
        transparency_score=transparency_score,
        independence_score=independence_score,
    )

    evaluation, _ = (
        SourceEvaluation.objects.update_or_create(
            signal=signal,
            source=source,
            defaults={
                "historical_accuracy_score": (
                    historical_accuracy_score
                ),
                "authority_score": authority_score,
                "transparency_score": transparency_score,
                "independence_score": independence_score,
                "reliability_score": reliability_score,
                "reliability_level": score_to_level(
                    reliability_score
                ),
                "notes": notes.strip(),
                "evaluated_by": evaluator,
            },
        )
    )

    evaluation.full_clean()
    evaluation.save()

    return evaluation


@transaction.atomic
def evaluate_information(
    *,
    signal: Signal,
    article: Article,
    evaluator,
    corroboration_score: float,
    consistency_score: float,
    specificity_score: float,
    timeliness_score: float,
    supports_signal: bool = True,
    contradiction_notes: str = "",
    notes: str = "",
) -> InformationEvaluation:
    ensure_article_supports_signal(
        signal=signal,
        article=article,
    )

    if (
        not supports_signal
        and not contradiction_notes.strip()
    ):
        raise ValidationError(
            "Catatan pertentangan wajib diisi apabila artikel "
            "tidak mendukung sinyal."
        )

    credibility_score = (
        calculate_information_credibility(
            corroboration_score=corroboration_score,
            consistency_score=consistency_score,
            specificity_score=specificity_score,
            timeliness_score=timeliness_score,
        )
    )

    evaluation, _ = (
        InformationEvaluation.objects.update_or_create(
            signal=signal,
            article=article,
            defaults={
                "corroboration_score": corroboration_score,
                "consistency_score": consistency_score,
                "specificity_score": specificity_score,
                "timeliness_score": timeliness_score,
                "credibility_score": credibility_score,
                "credibility_level": score_to_level(
                    credibility_score
                ),
                "supports_signal": supports_signal,
                "contradiction_notes": (
                    contradiction_notes.strip()
                ),
                "notes": notes.strip(),
                "evaluated_by": evaluator,
            },
        )
    )

    evaluation.full_clean()
    evaluation.save()

    return evaluation


def aggregate_source_reliability(
    signal: Signal,
) -> EvaluationAggregate:
    evaluations = signal.source_evaluations.all()

    scores = list(
        evaluations.values_list(
            "reliability_score",
            flat=True,
        )
    )

    if not scores:
        return EvaluationAggregate(
            score=0.0,
            count=0,
        )

    return EvaluationAggregate(
        score=round(
            sum(scores) / len(scores),
            4,
        ),
        count=len(scores),
    )


def aggregate_information_credibility(
    signal: Signal,
) -> EvaluationAggregate:
    evaluations = signal.information_evaluations.all()

    scores = list(
        evaluations.values_list(
            "credibility_score",
            flat=True,
        )
    )

    if not scores:
        return EvaluationAggregate(
            score=0.0,
            count=0,
        )

    return EvaluationAggregate(
        score=round(
            sum(scores) / len(scores),
            4,
        ),
        count=len(scores),
    )