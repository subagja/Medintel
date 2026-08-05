from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.signals.models import Signal, SignalHistory

from ..models import (
    EarlyWarning,
    EarlyWarningHistory,
    SignalAssessment,
)


PRIORITY_TO_WARNING_LEVEL = {
    SignalAssessment.RecommendedPriority.LOW: EarlyWarning.Level.MONITORING,
    SignalAssessment.RecommendedPriority.MEDIUM: EarlyWarning.Level.ADVISORY,
    SignalAssessment.RecommendedPriority.HIGH: EarlyWarning.Level.HIGH,
    SignalAssessment.RecommendedPriority.CRITICAL: EarlyWarning.Level.CRITICAL,
}


@dataclass(frozen=True)
class EarlyWarningEligibility:
    is_eligible: bool
    blockers: tuple[str, ...]
    recommended_level: str | None
    recommendation_applied: bool


@dataclass(frozen=True)
class EarlyWarningInput:
    title: str
    summary: str
    recommended_actions: str
    decision_notes: str


def assessment_recommendation_is_applied(
    assessment: SignalAssessment,
) -> bool:
    assessment_id = str(assessment.pk)

    for metadata in assessment.signal.histories.values_list(
        "metadata",
        flat=True,
    ):
        if not isinstance(metadata, dict):
            continue
        if (
            metadata.get("action") == "assessment_applied"
            and metadata.get("assessment_id") == assessment_id
        ):
            return True

    return False


def evaluate_early_warning_eligibility(
    assessment: SignalAssessment,
) -> EarlyWarningEligibility:
    blockers = []

    if assessment.signal.status not in {
        Signal.Status.VALIDATED,
        Signal.Status.CORRECTED,
        Signal.Status.ESCALATED,
    }:
        blockers.append(
            "Sinyal harus tervalidasi, dikoreksi, atau dieskalasi."
        )

    if assessment.status != SignalAssessment.Status.COMPLETED:
        blockers.append("Assessment harus berstatus selesai.")

    if not assessment.is_current:
        blockers.append("Assessment harus merupakan versi aktif.")

    recommendation_applied = assessment_recommendation_is_applied(
        assessment
    )
    if not recommendation_applied:
        blockers.append(
            "Rekomendasi assessment belum dikonfirmasi dan diterapkan analis."
        )

    if EarlyWarning.objects.filter(assessment=assessment).exists():
        blockers.append(
            "Peringatan dini untuk versi assessment ini sudah tersedia."
        )

    recommended_level = PRIORITY_TO_WARNING_LEVEL.get(
        assessment.recommended_priority
    )
    if recommended_level is None:
        blockers.append("Prioritas assessment belum dapat dipetakan.")

    return EarlyWarningEligibility(
        is_eligible=not blockers,
        blockers=tuple(blockers),
        recommended_level=recommended_level,
        recommendation_applied=recommendation_applied,
    )


def default_early_warning_initial(
    assessment: SignalAssessment,
) -> dict[str, str]:
    signal = assessment.signal
    return {
        "title": (
            f"Peringatan Dini {signal.primary_disease.name} — "
            f"{signal.primary_location.name}"
        ),
        "summary": signal.summary,
        "recommended_actions": (
            assessment.recommended_actions
            or signal.recommended_action
        ),
    }


def _next_warning_code() -> str:
    year = timezone.localdate().year
    prefix = f"PD-{year}-"
    codes = EarlyWarning.objects.filter(
        code__startswith=prefix
    ).values_list("code", flat=True)

    highest = 0
    for code in codes:
        try:
            highest = max(highest, int(code.rsplit("-", 1)[1]))
        except (IndexError, TypeError, ValueError):
            continue

    return f"{prefix}{highest + 1:04d}"


def _required(value: str, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValidationError(f"{label} wajib diisi.")
    return cleaned


@transaction.atomic
def issue_early_warning(
    *,
    assessment: SignalAssessment,
    analyst,
    warning_input: EarlyWarningInput,
) -> EarlyWarning:
    locked_assessment = (
        SignalAssessment.objects.select_for_update()
        .select_related(
            "signal__primary_disease",
            "signal__primary_location",
        )
        .get(pk=assessment.pk)
    )
    signal = Signal.objects.select_for_update().get(
        pk=locked_assessment.signal_id
    )

    eligibility = evaluate_early_warning_eligibility(
        locked_assessment
    )
    if not eligibility.is_eligible:
        raise ValidationError(" ".join(eligibility.blockers))

    title = _required(warning_input.title, "Judul peringatan")
    summary = _required(warning_input.summary, "Ringkasan eksekutif")
    recommended_actions = _required(
        warning_input.recommended_actions,
        "Rekomendasi tindakan",
    )
    decision_notes = _required(
        warning_input.decision_notes,
        "Dasar penerbitan analis",
    )

    previous_warning = (
        EarlyWarning.objects.select_for_update()
        .filter(signal=signal, is_current=True)
        .order_by("-version")
        .first()
    )
    next_version = 1
    if previous_warning:
        next_version = previous_warning.version + 1
        previous_status = previous_warning.status
        previous_warning.is_current = False
        if previous_warning.status == EarlyWarning.Status.ISSUED:
            previous_warning.status = EarlyWarning.Status.SUPERSEDED
        previous_warning.save(
            update_fields=["is_current", "status", "updated_at"]
        )
        EarlyWarningHistory.objects.create(
            warning=previous_warning,
            action=EarlyWarningHistory.Action.SUPERSEDED,
            from_status=previous_status,
            to_status=previous_warning.status,
            notes=(
                "Digantikan oleh peringatan dari assessment yang lebih baru."
            ),
            changed_by=analyst,
            metadata={
                "replacement_assessment_id": str(locked_assessment.pk),
            },
        )

    warning = EarlyWarning.objects.create(
        code=_next_warning_code(),
        signal=signal,
        assessment=locked_assessment,
        version=next_version,
        is_current=True,
        level=eligibility.recommended_level,
        confidence_level=locked_assessment.recommended_confidence,
        status=EarlyWarning.Status.ISSUED,
        title=title,
        summary=summary,
        analytical_judgement=locked_assessment.analytical_judgement,
        implications=locked_assessment.implications,
        recommended_actions=recommended_actions,
        information_gaps=locked_assessment.limitations,
        decision_notes=decision_notes,
        issued_by=analyst,
    )

    EarlyWarningHistory.objects.create(
        warning=warning,
        action=EarlyWarningHistory.Action.ISSUED,
        from_status="",
        to_status=EarlyWarning.Status.ISSUED,
        notes=decision_notes,
        changed_by=analyst,
        metadata={
            "assessment_id": str(locked_assessment.pk),
            "assessment_version": locked_assessment.version,
            "warning_level": warning.level,
            "confidence_level": warning.confidence_level,
        },
    )

    previous_signal_status = signal.status
    if warning.level in {
        EarlyWarning.Level.HIGH,
        EarlyWarning.Level.CRITICAL,
    }:
        signal.status = Signal.Status.ESCALATED
        signal.save(
            update_fields=["status", "updated_at", "last_updated_at"]
        )

    SignalHistory.objects.create(
        signal=signal,
        from_status=previous_signal_status,
        to_status=signal.status,
        changed_by=analyst,
        reason=decision_notes,
        metadata={
            "action": "early_warning_issued",
            "warning_id": str(warning.pk),
            "warning_code": warning.code,
            "warning_level": warning.level,
            "assessment_id": str(locked_assessment.pk),
        },
    )

    return warning


@transaction.atomic
def close_early_warning(
    *,
    warning: EarlyWarning,
    analyst,
    notes: str,
) -> EarlyWarning:
    locked_warning = (
        EarlyWarning.objects.select_for_update()
        .select_related("signal")
        .get(pk=warning.pk)
    )

    if locked_warning.status != EarlyWarning.Status.ISSUED:
        raise ValidationError(
            "Hanya peringatan yang masih diterbitkan dapat ditutup."
        )

    closure_notes = _required(notes, "Dasar penutupan")
    previous_status = locked_warning.status
    locked_warning.status = EarlyWarning.Status.CLOSED
    locked_warning.closed_by = analyst
    locked_warning.closed_at = timezone.now()
    locked_warning.closure_notes = closure_notes
    locked_warning.save(
        update_fields=[
            "status",
            "closed_by",
            "closed_at",
            "closure_notes",
            "updated_at",
        ]
    )

    EarlyWarningHistory.objects.create(
        warning=locked_warning,
        action=EarlyWarningHistory.Action.CLOSED,
        from_status=previous_status,
        to_status=EarlyWarning.Status.CLOSED,
        notes=closure_notes,
        changed_by=analyst,
    )

    SignalHistory.objects.create(
        signal=locked_warning.signal,
        from_status=locked_warning.signal.status,
        to_status=locked_warning.signal.status,
        changed_by=analyst,
        reason=closure_notes,
        metadata={
            "action": "early_warning_closed",
            "warning_id": str(locked_warning.pk),
            "warning_code": locked_warning.code,
        },
    )

    return locked_warning
