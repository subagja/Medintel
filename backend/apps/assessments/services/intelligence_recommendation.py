from dataclasses import dataclass
from datetime import date

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.signals.models import Signal

from ..models import (
    EarlyWarning,
    IntelligenceRecommendation,
    IntelligenceRecommendationHistory,
    SignalAssessment,
)
from .early_warning import assessment_recommendation_is_applied


PRIORITY_TO_URGENCY = {
    SignalAssessment.RecommendedPriority.LOW: (
        IntelligenceRecommendation.Urgency.ROUTINE
    ),
    SignalAssessment.RecommendedPriority.MEDIUM: (
        IntelligenceRecommendation.Urgency.PRIORITY
    ),
    SignalAssessment.RecommendedPriority.HIGH: (
        IntelligenceRecommendation.Urgency.URGENT
    ),
    SignalAssessment.RecommendedPriority.CRITICAL: (
        IntelligenceRecommendation.Urgency.IMMEDIATE
    ),
}


PRIORITY_TO_ACTION_CATEGORY = {
    SignalAssessment.RecommendedPriority.LOW: (
        IntelligenceRecommendation.ActionCategory.MONITORING
    ),
    SignalAssessment.RecommendedPriority.MEDIUM: (
        IntelligenceRecommendation.ActionCategory.VERIFICATION
    ),
    SignalAssessment.RecommendedPriority.HIGH: (
        IntelligenceRecommendation.ActionCategory.COORDINATION
    ),
    SignalAssessment.RecommendedPriority.CRITICAL: (
        IntelligenceRecommendation.ActionCategory.PREPAREDNESS
    ),
}


@dataclass(frozen=True)
class IntelligenceRecommendationEligibility:
    is_eligible: bool
    blockers: tuple[str, ...]
    recommendation_applied: bool
    recommended_urgency: str | None


@dataclass(frozen=True)
class IntelligenceRecommendationInput:
    title: str
    situation_summary: str
    objective: str
    recommended_action: str
    action_category: str
    urgency: str
    target_unit: str
    due_date: date | None
    success_indicators: str
    assumptions: str = ""
    information_gaps: str = ""


def _required(value: str, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValidationError(f"{label} wajib diisi.")
    return cleaned


def _warning_for_assessment(
    assessment: SignalAssessment,
) -> EarlyWarning | None:
    try:
        warning = assessment.early_warning
    except ObjectDoesNotExist:
        return None
    return warning if warning.is_current else None


def evaluate_intelligence_recommendation_eligibility(
    assessment: SignalAssessment,
) -> IntelligenceRecommendationEligibility:
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
            "Rekomendasi assessment belum dikonfirmasi dan diterapkan "
            "analis."
        )

    if IntelligenceRecommendation.objects.filter(
        assessment=assessment
    ).exists():
        blockers.append(
            "Rekomendasi intelijen untuk versi assessment ini sudah "
            "tersedia."
        )

    recommended_urgency = PRIORITY_TO_URGENCY.get(
        assessment.recommended_priority
    )
    if recommended_urgency is None:
        blockers.append("Prioritas assessment belum dapat dipetakan.")

    return IntelligenceRecommendationEligibility(
        is_eligible=not blockers,
        blockers=tuple(blockers),
        recommendation_applied=recommendation_applied,
        recommended_urgency=recommended_urgency,
    )


def default_intelligence_recommendation_initial(
    assessment: SignalAssessment,
) -> dict[str, object]:
    signal = assessment.signal
    disease = signal.primary_disease.name
    location = signal.primary_location.name
    priority_label = assessment.get_recommended_priority_display()

    return {
        "title": f"Rekomendasi Intelijen {disease} — {location}",
        "situation_summary": (
            assessment.analytical_judgement or signal.summary
        ),
        "objective": (
            f"Mendukung verifikasi dan pemantauan perkembangan {disease} "
            f"di {location} sesuai tingkat perhatian {priority_label}."
        ),
        "recommended_action": (
            assessment.recommended_actions
            or signal.recommended_action
        ),
        "action_category": PRIORITY_TO_ACTION_CATEGORY.get(
            assessment.recommended_priority,
            IntelligenceRecommendation.ActionCategory.VERIFICATION,
        ),
        "urgency": PRIORITY_TO_URGENCY.get(
            assessment.recommended_priority,
            IntelligenceRecommendation.Urgency.PRIORITY,
        ),
        "success_indicators": (
            "Tersedia konfirmasi data resmi, sumber pembanding, dan "
            "pembaruan situasi yang dapat ditelusuri."
        ),
        "assumptions": assessment.assumptions,
        "information_gaps": assessment.limitations,
    }


def _next_recommendation_code() -> str:
    year = timezone.localdate().year
    prefix = f"RI-{year}-"
    codes = IntelligenceRecommendation.objects.filter(
        code__startswith=prefix
    ).values_list("code", flat=True)

    highest = 0
    for code in codes:
        try:
            highest = max(highest, int(code.rsplit("-", 1)[1]))
        except (IndexError, TypeError, ValueError):
            continue

    return f"{prefix}{highest + 1:04d}"


def _validate_due_date(due_date: date | None) -> None:
    if due_date and due_date < timezone.localdate():
        raise ValidationError(
            "Tenggat rekomendasi tidak boleh berada di masa lalu."
        )


@transaction.atomic
def create_intelligence_recommendation_draft(
    *,
    assessment: SignalAssessment,
    analyst,
    recommendation_input: IntelligenceRecommendationInput,
) -> IntelligenceRecommendation:
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

    eligibility = evaluate_intelligence_recommendation_eligibility(
        locked_assessment
    )
    if not eligibility.is_eligible:
        raise ValidationError(" ".join(eligibility.blockers))

    title = _required(recommendation_input.title, "Judul rekomendasi")
    situation_summary = _required(
        recommendation_input.situation_summary,
        "Ringkasan situasi",
    )
    objective = _required(recommendation_input.objective, "Tujuan")
    recommended_action = _required(
        recommendation_input.recommended_action,
        "Tindakan yang direkomendasikan",
    )
    target_unit = _required(
        recommendation_input.target_unit,
        "Sasaran rekomendasi",
    )
    success_indicators = _required(
        recommendation_input.success_indicators,
        "Indikator keberhasilan",
    )

    valid_categories = {
        value
        for value, _label in IntelligenceRecommendation.ActionCategory.choices
    }
    if recommendation_input.action_category not in valid_categories:
        raise ValidationError("Kategori tindakan tidak valid.")

    valid_urgencies = {
        value
        for value, _label in IntelligenceRecommendation.Urgency.choices
    }
    if recommendation_input.urgency not in valid_urgencies:
        raise ValidationError("Urgensi rekomendasi tidak valid.")

    _validate_due_date(recommendation_input.due_date)

    previous_recommendation = (
        IntelligenceRecommendation.objects.select_for_update()
        .filter(signal=signal, is_current=True)
        .order_by("-version")
        .first()
    )
    next_version = 1
    if previous_recommendation:
        next_version = previous_recommendation.version + 1
        previous_status = previous_recommendation.status
        previous_recommendation.status = (
            IntelligenceRecommendation.Status.SUPERSEDED
        )
        previous_recommendation.is_current = False
        previous_recommendation.save(
            update_fields=["status", "is_current", "updated_at"]
        )
        IntelligenceRecommendationHistory.objects.create(
            recommendation=previous_recommendation,
            action=(
                IntelligenceRecommendationHistory.Action.SUPERSEDED
            ),
            from_status=previous_status,
            to_status=IntelligenceRecommendation.Status.SUPERSEDED,
            notes=(
                "Digantikan oleh rekomendasi dari assessment yang lebih "
                "baru."
            ),
            changed_by=analyst,
            metadata={
                "replacement_assessment_id": str(locked_assessment.pk),
            },
        )

    recommendation = IntelligenceRecommendation.objects.create(
        code=_next_recommendation_code(),
        signal=signal,
        assessment=locked_assessment,
        early_warning=_warning_for_assessment(locked_assessment),
        version=next_version,
        is_current=True,
        status=IntelligenceRecommendation.Status.DRAFT,
        urgency=recommendation_input.urgency,
        action_category=recommendation_input.action_category,
        title=title,
        situation_summary=situation_summary,
        objective=objective,
        recommended_action=recommended_action,
        target_unit=target_unit,
        due_date=recommendation_input.due_date,
        success_indicators=success_indicators,
        assumptions=(recommendation_input.assumptions or "").strip(),
        information_gaps=(
            recommendation_input.information_gaps or ""
        ).strip(),
        created_by=analyst,
    )

    IntelligenceRecommendationHistory.objects.create(
        recommendation=recommendation,
        action=IntelligenceRecommendationHistory.Action.DRAFTED,
        from_status="",
        to_status=IntelligenceRecommendation.Status.DRAFT,
        notes="Draf dibentuk dari assessment terkonfirmasi.",
        changed_by=analyst,
        metadata={
            "assessment_id": str(locked_assessment.pk),
            "assessment_version": locked_assessment.version,
            "early_warning_id": (
                str(recommendation.early_warning_id)
                if recommendation.early_warning_id
                else ""
            ),
        },
    )

    return recommendation


def _locked_current_recommendation(
    recommendation: IntelligenceRecommendation,
) -> IntelligenceRecommendation:
    locked = IntelligenceRecommendation.objects.select_for_update().get(
        pk=recommendation.pk
    )
    if not locked.is_current:
        raise ValidationError(
            "Hanya rekomendasi versi aktif yang dapat diperbarui."
        )
    return locked


def _record_history(
    *,
    recommendation: IntelligenceRecommendation,
    action: str,
    from_status: str,
    notes: str,
    analyst,
) -> None:
    IntelligenceRecommendationHistory.objects.create(
        recommendation=recommendation,
        action=action,
        from_status=from_status,
        to_status=recommendation.status,
        notes=notes,
        changed_by=analyst,
        metadata={
            "assessment_id": str(recommendation.assessment_id),
            "recommendation_version": recommendation.version,
        },
    )


@transaction.atomic
def approve_intelligence_recommendation(
    *,
    recommendation: IntelligenceRecommendation,
    analyst,
    notes: str,
) -> IntelligenceRecommendation:
    locked = _locked_current_recommendation(recommendation)
    if locked.status != IntelligenceRecommendation.Status.DRAFT:
        raise ValidationError(
            "Hanya rekomendasi berstatus draf yang dapat ditetapkan."
        )

    decision_notes = _required(notes, "Dasar penetapan")
    previous_status = locked.status
    locked.status = IntelligenceRecommendation.Status.APPROVED
    locked.decision_rationale = decision_notes
    locked.approved_by = analyst
    locked.approved_at = timezone.now()
    locked.save(
        update_fields=[
            "status",
            "decision_rationale",
            "approved_by",
            "approved_at",
            "updated_at",
        ]
    )
    _record_history(
        recommendation=locked,
        action=IntelligenceRecommendationHistory.Action.APPROVED,
        from_status=previous_status,
        notes=decision_notes,
        analyst=analyst,
    )
    return locked


@transaction.atomic
def start_intelligence_recommendation(
    *,
    recommendation: IntelligenceRecommendation,
    analyst,
    notes: str,
) -> IntelligenceRecommendation:
    locked = _locked_current_recommendation(recommendation)
    if locked.status != IntelligenceRecommendation.Status.APPROVED:
        raise ValidationError(
            "Hanya rekomendasi yang telah ditetapkan dapat mulai "
            "ditindaklanjuti."
        )

    progress_notes = _required(notes, "Catatan tindak lanjut")
    previous_status = locked.status
    locked.status = IntelligenceRecommendation.Status.IN_PROGRESS
    locked.save(update_fields=["status", "updated_at"])
    _record_history(
        recommendation=locked,
        action=IntelligenceRecommendationHistory.Action.STARTED,
        from_status=previous_status,
        notes=progress_notes,
        analyst=analyst,
    )
    return locked


@transaction.atomic
def complete_intelligence_recommendation(
    *,
    recommendation: IntelligenceRecommendation,
    analyst,
    notes: str,
) -> IntelligenceRecommendation:
    locked = _locked_current_recommendation(recommendation)
    if locked.status not in {
        IntelligenceRecommendation.Status.APPROVED,
        IntelligenceRecommendation.Status.IN_PROGRESS,
    }:
        raise ValidationError(
            "Hanya rekomendasi yang ditetapkan atau sedang "
            "ditindaklanjuti dapat diselesaikan."
        )

    completion_notes = _required(notes, "Hasil tindak lanjut")
    previous_status = locked.status
    locked.status = IntelligenceRecommendation.Status.COMPLETED
    locked.is_current = False
    locked.completed_by = analyst
    locked.completed_at = timezone.now()
    locked.completion_notes = completion_notes
    locked.save(
        update_fields=[
            "status",
            "is_current",
            "completed_by",
            "completed_at",
            "completion_notes",
            "updated_at",
        ]
    )
    _record_history(
        recommendation=locked,
        action=IntelligenceRecommendationHistory.Action.COMPLETED,
        from_status=previous_status,
        notes=completion_notes,
        analyst=analyst,
    )
    return locked


@transaction.atomic
def cancel_intelligence_recommendation(
    *,
    recommendation: IntelligenceRecommendation,
    analyst,
    notes: str,
) -> IntelligenceRecommendation:
    locked = _locked_current_recommendation(recommendation)
    if locked.status not in {
        IntelligenceRecommendation.Status.DRAFT,
        IntelligenceRecommendation.Status.APPROVED,
        IntelligenceRecommendation.Status.IN_PROGRESS,
    }:
        raise ValidationError(
            "Rekomendasi dengan status ini tidak dapat dibatalkan."
        )

    cancellation_reason = _required(notes, "Alasan pembatalan")
    previous_status = locked.status
    locked.status = IntelligenceRecommendation.Status.CANCELED
    locked.is_current = False
    locked.cancellation_reason = cancellation_reason
    locked.save(
        update_fields=[
            "status",
            "is_current",
            "cancellation_reason",
            "updated_at",
        ]
    )
    _record_history(
        recommendation=locked,
        action=IntelligenceRecommendationHistory.Action.CANCELED,
        from_status=previous_status,
        notes=cancellation_reason,
        analyst=analyst,
    )
    return locked
