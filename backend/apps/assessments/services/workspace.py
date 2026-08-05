from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.signals.models import Signal, SignalArticle

from ..models import ArticleValidationAssessment
from .evaluation import evaluate_information, evaluate_source


RELIABILITY_SCORE_MAP = {
    ArticleValidationAssessment.SourceReliability.A: 0.95,
    ArticleValidationAssessment.SourceReliability.B: 0.80,
    ArticleValidationAssessment.SourceReliability.C: 0.65,
    ArticleValidationAssessment.SourceReliability.D: 0.40,
    ArticleValidationAssessment.SourceReliability.E: 0.20,
}

CREDIBILITY_SCORE_MAP = {
    ArticleValidationAssessment.InformationCredibility.CONFIRMED: 0.95,
    ArticleValidationAssessment.InformationCredibility.PROBABLY_TRUE: 0.80,
    ArticleValidationAssessment.InformationCredibility.POSSIBLY_TRUE: 0.65,
    ArticleValidationAssessment.InformationCredibility.DOUBTFUL: 0.40,
    ArticleValidationAssessment.InformationCredibility.IMPROBABLE: 0.20,
}


@dataclass(frozen=True)
class EvaluationSyncResult:
    source_count: int
    information_count: int
    admiralty_codes: tuple[str, ...]


def _validated_balance(article):
    try:
        balance = article.validation_assessment
    except ArticleValidationAssessment.DoesNotExist as exc:
        raise ValidationError(
            f'Artikel "{article.title}" belum memiliki Neraca Informasi.'
        ) from exc

    if balance.validation_status != (
        ArticleValidationAssessment.ValidationStatus.VALIDATED
    ):
        raise ValidationError(
            f'Neraca Informasi artikel "{article.title}" belum dikonfirmasi.'
        )

    if balance.source_reliability not in RELIABILITY_SCORE_MAP:
        raise ValidationError(
            f'Reliabilitas sumber artikel "{article.title}" masih F '
            '(belum dapat dinilai).'
        )

    if balance.information_credibility not in CREDIBILITY_SCORE_MAP:
        raise ValidationError(
            f'Kredibilitas artikel "{article.title}" masih 6 '
            '(belum dapat dinilai).'
        )

    return balance


@transaction.atomic
def sync_signal_evaluations_from_information_balance(
    *,
    signal: Signal,
    evaluator,
) -> EvaluationSyncResult:
    """
    Membawa Neraca Informasi yang sudah dikonfirmasi ke assessment sinyal.

    Nilai Admiralty diterjemahkan menjadi baseline numerik yang transparan.
    Ini mencegah analis mengulang penilaian sumber dan informasi yang sama.
    """
    relations = list(
        signal.signal_articles.select_related(
            "article__source",
            "article__validation_assessment",
        ).order_by("article__published_at", "article__created_at")
    )

    if not relations:
        raise ValidationError("Sinyal tidak memiliki artikel pendukung.")

    admiralty_codes = []
    source_ids = set()

    for relation in relations:
        article = relation.article
        balance = _validated_balance(article)
        reliability_score = RELIABILITY_SCORE_MAP[
            balance.source_reliability
        ]
        credibility_score = CREDIBILITY_SCORE_MAP[
            balance.information_credibility
        ]
        code = balance.admiralty_code
        admiralty_codes.append(code)

        evaluate_source(
            signal=signal,
            source=article.source,
            evaluator=evaluator,
            historical_accuracy_score=reliability_score,
            authority_score=reliability_score,
            transparency_score=reliability_score,
            independence_score=reliability_score,
            notes=(
                f"Baseline assessment diturunkan dari Neraca Informasi "
                f"{code} yang telah dikonfirmasi analis."
            ),
        )
        source_ids.add(article.source_id)

        contradicts = (
            relation.support_type
            == SignalArticle.SupportType.CONTRADICTING
        )
        evaluate_information(
            signal=signal,
            article=article,
            evaluator=evaluator,
            corroboration_score=credibility_score,
            consistency_score=credibility_score,
            specificity_score=credibility_score,
            timeliness_score=credibility_score,
            supports_signal=not contradicts,
            contradiction_notes=(
                "Artikel ditandai sebagai sumber bertentangan pada sinyal."
                if contradicts
                else ""
            ),
            notes=(
                f"Baseline assessment diturunkan dari Neraca Informasi "
                f"{code} yang telah dikonfirmasi analis."
            ),
        )

    return EvaluationSyncResult(
        source_count=len(source_ids),
        information_count=len(relations),
        admiralty_codes=tuple(admiralty_codes),
    )
