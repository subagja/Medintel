from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.signals.models import Signal, SignalHistory

from ..models import SignalAssessment
from .evaluation import (
    aggregate_information_credibility,
    aggregate_source_reliability,
)
from .scoring import calculate_assessment_scores


@dataclass(frozen=True)
class SignalAssessmentInput:
    urgency_score: int
    impact_score: int
    geographic_scope_score: int
    development_speed_score: int
    vulnerability_score: int
    information_completeness_score: float
    evidence_consistency_score: float
    analytical_judgement: str
    implications: str = ""
    recommended_actions: str = ""
    assumptions: str = ""
    limitations: str = ""


def validate_signal_for_assessment(
    signal: Signal,
) -> None:
    allowed_statuses = {
        Signal.Status.VALIDATED,
        Signal.Status.CORRECTED,
        Signal.Status.ESCALATED,
    }

    if signal.status not in allowed_statuses:
        raise ValidationError(
            "Sinyal harus divalidasi, dikoreksi, atau dieskalasi "
            "sebelum dinilai."
        )

    if not signal.signal_articles.exists():
        raise ValidationError(
            "Sinyal tidak memiliki artikel pendukung."
        )

    if not signal.signal_indicators.exists():
        raise ValidationError(
            "Sinyal tidak memiliki indikator pendukung."
        )


@transaction.atomic
def create_signal_assessment(
    *,
    signal: Signal,
    assessor,
    assessment_input: SignalAssessmentInput,
    complete: bool = True,
) -> SignalAssessment:
    locked_signal = (
        Signal.objects.select_for_update()
        .get(pk=signal.pk)
    )

    validate_signal_for_assessment(
        locked_signal
    )

    if not assessment_input.analytical_judgement.strip():
        raise ValidationError(
            "Judgement analitis wajib diisi."
        )

    source_aggregate = (
        aggregate_source_reliability(
            locked_signal
        )
    )

    information_aggregate = (
        aggregate_information_credibility(
            locked_signal
        )
    )

    if source_aggregate.count == 0:
        raise ValidationError(
            "Belum terdapat evaluasi reliabilitas sumber."
        )

    if information_aggregate.count == 0:
        raise ValidationError(
            "Belum terdapat evaluasi kredibilitas informasi."
        )

    scores = calculate_assessment_scores(
        urgency_score=(
            assessment_input.urgency_score
        ),
        impact_score=(
            assessment_input.impact_score
        ),
        geographic_scope_score=(
            assessment_input.geographic_scope_score
        ),
        development_speed_score=(
            assessment_input.development_speed_score
        ),
        vulnerability_score=(
            assessment_input.vulnerability_score
        ),
        source_reliability_score=(
            source_aggregate.score
        ),
        information_credibility_score=(
            information_aggregate.score
        ),
        information_completeness_score=(
            assessment_input.information_completeness_score
        ),
        evidence_consistency_score=(
            assessment_input.evidence_consistency_score
        ),
    )

    previous_assessment = (
        SignalAssessment.objects.filter(
            signal=locked_signal,
            is_current=True,
        )
        .order_by("-version")
        .first()
    )

    if previous_assessment:
        next_version = (
            previous_assessment.version + 1
        )

        previous_assessment.is_current = False
        previous_assessment.status = (
            SignalAssessment.Status.SUPERSEDED
        )

        previous_assessment.save(
            update_fields=[
                "is_current",
                "status",
                "updated_at",
            ]
        )
    else:
        next_version = 1

    assessment = SignalAssessment.objects.create(
        signal=locked_signal,
        version=next_version,
        is_current=True,
        status=(
            SignalAssessment.Status.COMPLETED
            if complete
            else SignalAssessment.Status.DRAFT
        ),
        urgency_score=assessment_input.urgency_score,
        impact_score=assessment_input.impact_score,
        geographic_scope_score=(
            assessment_input.geographic_scope_score
        ),
        development_speed_score=(
            assessment_input.development_speed_score
        ),
        vulnerability_score=(
            assessment_input.vulnerability_score
        ),
        source_reliability_score=(
            source_aggregate.score
        ),
        information_credibility_score=(
            information_aggregate.score
        ),
        information_completeness_score=(
            assessment_input.information_completeness_score
        ),
        evidence_consistency_score=(
            assessment_input.evidence_consistency_score
        ),
        priority_score=scores.priority_score,
        confidence_score=scores.confidence_score,
        recommended_priority=(
            scores.recommended_priority
        ),
        recommended_confidence=(
            scores.recommended_confidence
        ),
        analytical_judgement=(
            assessment_input.analytical_judgement.strip()
        ),
        implications=assessment_input.implications.strip(),
        recommended_actions=(
            assessment_input.recommended_actions.strip()
        ),
        assumptions=assessment_input.assumptions.strip(),
        limitations=assessment_input.limitations.strip(),
        assessed_by=assessor,
        completed_at=(
            timezone.now()
            if complete
            else None
        ),
    )

    SignalHistory.objects.create(
        signal=locked_signal,
        from_status=locked_signal.status,
        to_status=locked_signal.status,
        changed_by=assessor,
        reason=(
            f"Signal assessment v{assessment.version} dibuat."
        ),
        metadata={
            "action": "assessment_created",
            "assessment_id": str(assessment.id),
            "assessment_version": assessment.version,
            "priority_score": assessment.priority_score,
            "confidence_score": assessment.confidence_score,
            "recommended_priority": (
                assessment.recommended_priority
            ),
            "recommended_confidence": (
                assessment.recommended_confidence
            ),
        },
    )

    return assessment


@transaction.atomic
def apply_assessment_recommendation(
    *,
    assessment: SignalAssessment,
    analyst,
    notes: str,
) -> Signal:
    if assessment.status != (
        SignalAssessment.Status.COMPLETED
    ):
        raise ValidationError(
            "Hanya assessment yang selesai dapat diterapkan."
        )

    if not assessment.is_current:
        raise ValidationError(
            "Assessment bukan versi aktif."
        )

    if not notes.strip():
        raise ValidationError(
            "Catatan keputusan analis wajib diisi."
        )

    signal = (
        Signal.objects.select_for_update()
        .get(pk=assessment.signal_id)
    )

    priority_mapping = {
        SignalAssessment.RecommendedPriority.LOW: (
            Signal.PriorityLevel.LOW
        ),
        SignalAssessment.RecommendedPriority.MEDIUM: (
            Signal.PriorityLevel.MEDIUM
        ),
        SignalAssessment.RecommendedPriority.HIGH: (
            Signal.PriorityLevel.HIGH
        ),
        SignalAssessment.RecommendedPriority.CRITICAL: (
            Signal.PriorityLevel.CRITICAL
        ),
    }

    confidence_mapping = {
        SignalAssessment.RecommendedConfidence.LOW: (
            Signal.ConfidenceLevel.LOW
        ),
        SignalAssessment.RecommendedConfidence.MEDIUM: (
            Signal.ConfidenceLevel.MEDIUM
        ),
        SignalAssessment.RecommendedConfidence.HIGH: (
            Signal.ConfidenceLevel.HIGH
        ),
    }

    previous_priority = signal.priority_level
    previous_confidence = signal.confidence_level

    signal.priority_level = priority_mapping[
        assessment.recommended_priority
    ]

    signal.confidence_level = confidence_mapping[
        assessment.recommended_confidence
    ]

    signal.analyst_judgement = (
        assessment.analytical_judgement
    )

    signal.implication = assessment.implications

    signal.recommended_action = (
        assessment.recommended_actions
    )

    signal.save(
        update_fields=[
            "priority_level",
            "confidence_level",
            "analyst_judgement",
            "implication",
            "recommended_action",
            "updated_at",
            "last_updated_at",
        ]
    )

    SignalHistory.objects.create(
        signal=signal,
        from_status=signal.status,
        to_status=signal.status,
        changed_by=analyst,
        reason=notes.strip(),
        metadata={
            "action": "assessment_applied",
            "assessment_id": str(assessment.id),
            "previous_priority": previous_priority,
            "new_priority": signal.priority_level,
            "previous_confidence": previous_confidence,
            "new_confidence": signal.confidence_level,
        },
    )

    return signal


