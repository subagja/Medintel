from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import Signal, SignalHistory


ALLOWED_TRANSITIONS = {
    Signal.Status.DRAFT: {
        Signal.Status.NEEDS_REVIEW,
        Signal.Status.REJECTED,
    },
    Signal.Status.NEEDS_REVIEW: {
        Signal.Status.UNDER_REVIEW,
        Signal.Status.REJECTED,
    },
    Signal.Status.UNDER_REVIEW: {
        Signal.Status.VALIDATED,
        Signal.Status.CORRECTED,
        Signal.Status.REJECTED,
    },
    Signal.Status.VALIDATED: {
        Signal.Status.ESCALATED,
        Signal.Status.CLOSED,
        Signal.Status.CORRECTED,
    },
    Signal.Status.CORRECTED: {
        Signal.Status.VALIDATED,
        Signal.Status.ESCALATED,
        Signal.Status.CLOSED,
    },
    Signal.Status.ESCALATED: {
        Signal.Status.CLOSED,
        Signal.Status.CORRECTED,
    },
    Signal.Status.REJECTED: set(),
    Signal.Status.CLOSED: set(),
}


SIGNAL_CORRECTION_FIELDS = {
    "title",
    "summary",
    "primary_disease",
    "primary_location",
    "event_start_date",
    "event_end_date",
    "priority_level",
    "confidence_level",
    "event_classification",
    "classification_basis",
    "analyst_judgement",
    "implication",
    "recommended_action",
    "information_gaps",
}


def validate_active_user(user, role_name: str = "Pengguna") -> None:
    if user is None:
        raise ValidationError(
            f"{role_name} wajib ditentukan."
        )

    if not getattr(user, "is_active", False):
        raise ValidationError(
            f"{role_name} tidak aktif."
        )


def serialize_value(value: Any) -> Any:
    if value is None:
        return None

    if hasattr(value, "isoformat"):
        return value.isoformat()

    if hasattr(value, "pk"):
        return str(value.pk)

    return value


def signal_snapshot(signal: Signal) -> dict[str, Any]:
    return {
        "code": signal.code,
        "title": signal.title,
        "summary": signal.summary,
        "primary_disease_id": str(signal.primary_disease_id),
        "primary_location_id": str(signal.primary_location_id),
        "event_start_date": serialize_value(
            signal.event_start_date
        ),
        "event_end_date": serialize_value(
            signal.event_end_date
        ),
        "status": signal.status,
        "priority_level": signal.priority_level,
        "confidence_level": signal.confidence_level,
        "event_classification": signal.event_classification,
        "classification_basis": signal.classification_basis,
        "system_score": signal.system_score,
        "assigned_to_id": (
            str(signal.assigned_to_id)
            if signal.assigned_to_id
            else None
        ),
        "validated_by_id": (
            str(signal.validated_by_id)
            if signal.validated_by_id
            else None
        ),
        "validated_at": serialize_value(
            signal.validated_at
        ),
        "validation_notes": signal.validation_notes,
        "analyst_judgement": signal.analyst_judgement,
        "implication": signal.implication,
        "recommended_action": signal.recommended_action,
        "information_gaps": signal.information_gaps,
        "closed_at": serialize_value(signal.closed_at),
    }


def validate_transition(
    *,
    signal: Signal,
    target_status: str,
) -> None:
    allowed_targets = ALLOWED_TRANSITIONS.get(
        signal.status,
        set(),
    )

    if target_status not in allowed_targets:
        raise ValidationError(
            (
                "Perubahan status tidak diperbolehkan: "
                f"{signal.status} → {target_status}."
            )
        )


def record_signal_history(
    *,
    signal: Signal,
    from_status: str,
    to_status: str,
    changed_by,
    reason: str = "",
    metadata: dict | None = None,
) -> SignalHistory:
    return SignalHistory.objects.create(
        signal=signal,
        from_status=from_status,
        to_status=to_status,
        changed_by=changed_by,
        reason=reason.strip(),
        metadata=metadata or {},
    )

@transaction.atomic
def assign_signal(
    *,
    signal: Signal,
    analyst,
    assigned_by,
    notes: str = "",
) -> Signal:
    validate_active_user(
        analyst,
        "Analis",
    )
    validate_active_user(
        assigned_by,
        "Pemberi penugasan",
    )

    if signal.status in {
        Signal.Status.REJECTED,
        Signal.Status.CLOSED,
    }:
        raise ValidationError(
            "Sinyal yang ditolak atau ditutup tidak dapat ditugaskan."
        )

    previous_assignee_id = (
        str(signal.assigned_to_id)
        if signal.assigned_to_id
        else None
    )

    signal.assigned_to = analyst
    signal.save(
        update_fields=[
            "assigned_to",
            "updated_at",
            "last_updated_at",
        ]
    )

    SignalHistory.objects.create(
        signal=signal,
        from_status=signal.status,
        to_status=signal.status,
        changed_by=assigned_by,
        reason=notes.strip() or "Penugasan analis.",
        metadata={
            "action": "assign",
            "previous_assignee_id": previous_assignee_id,
            "new_assignee_id": str(analyst.pk),
        },
    )

    return signal


@transaction.atomic
def start_signal_review(
    *,
    signal: Signal,
    reviewer,
    notes: str = "",
) -> Signal:
    validate_active_user(
        reviewer,
        "Reviewer",
    )

    validate_transition(
        signal=signal,
        target_status=Signal.Status.UNDER_REVIEW,
    )

    if (
        signal.assigned_to_id
        and signal.assigned_to_id != reviewer.pk
    ):
        raise ValidationError(
            "Sinyal telah ditugaskan kepada analis lain."
        )

    previous_status = signal.status

    signal.status = Signal.Status.UNDER_REVIEW
    signal.assigned_to = reviewer

    signal.save(
        update_fields=[
            "status",
            "assigned_to",
            "updated_at",
            "last_updated_at",
        ]
    )

    record_signal_history(
        signal=signal,
        from_status=previous_status,
        to_status=signal.status,
        changed_by=reviewer,
        reason=notes or "Review sinyal dimulai.",
    )

    return signal


@transaction.atomic
def validate_signal(
    *,
    signal: Signal,
    reviewer,
    judgement: str,
    implication: str = "",
    recommended_action: str = "",
    information_gaps: str = "",
    notes: str = "",
) -> Signal:
    validate_active_user(
        reviewer,
        "Reviewer",
    )

    validate_transition(
        signal=signal,
        target_status=Signal.Status.VALIDATED,
    )

    if not judgement.strip():
        raise ValidationError(
            "Judgement analis wajib diisi."
        )

    if not signal.signal_indicators.exists():
        raise ValidationError(
            "Sinyal tidak memiliki indikator pendukung."
        )

    if not signal.signal_articles.exists():
        raise ValidationError(
            "Sinyal tidak memiliki artikel pendukung."
        )

    if not signal.signal_requirements.exists():
        raise ValidationError(
            "Sinyal belum terkait kebutuhan intelijen."
        )

    previous_status = signal.status

    signal.status = Signal.Status.VALIDATED
    signal.validated_by = reviewer
    signal.validated_at = timezone.now()
    signal.validation_notes = notes.strip()
    signal.analyst_judgement = judgement.strip()
    signal.implication = implication.strip()
    signal.recommended_action = recommended_action.strip()
    signal.information_gaps = information_gaps.strip()

    signal.full_clean()
    signal.save(
        update_fields=[
            "status",
            "validated_by",
            "validated_at",
            "validation_notes",
            "analyst_judgement",
            "implication",
            "recommended_action",
            "information_gaps",
            "updated_at",
            "last_updated_at",
        ]
    )

    record_signal_history(
        signal=signal,
        from_status=previous_status,
        to_status=signal.status,
        changed_by=reviewer,
        reason=notes or "Sinyal divalidasi analis.",
        metadata={
            "judgement": signal.analyst_judgement,
            "implication": signal.implication,
            "recommended_action": signal.recommended_action,
            "information_gaps": signal.information_gaps,
        },
    )

    return signal


@transaction.atomic
def correct_signal(
    *,
    signal: Signal,
    reviewer,
    corrections: dict[str, Any],
    notes: str,
) -> Signal:
    validate_active_user(
        reviewer,
        "Reviewer",
    )

    if signal.status not in {
        Signal.Status.UNDER_REVIEW,
        Signal.Status.VALIDATED,
        Signal.Status.CORRECTED,
        Signal.Status.ESCALATED,
    }:
        raise ValidationError(
            "Sinyal pada status ini tidak dapat dikoreksi."
        )

    if not corrections:
        raise ValidationError(
            "Data koreksi sinyal tidak boleh kosong."
        )

    if not notes.strip():
        raise ValidationError(
            "Alasan koreksi sinyal wajib diisi."
        )

    unknown_fields = (
        set(corrections)
        - SIGNAL_CORRECTION_FIELDS
    )

    if unknown_fields:
        raise ValidationError(
            "Field koreksi tidak diperbolehkan: "
            + ", ".join(sorted(unknown_fields))
        )

    before_data = signal_snapshot(signal)
    previous_status = signal.status

    for field_name, value in corrections.items():
        setattr(
            signal,
            field_name,
            value,
        )

    signal.status = Signal.Status.CORRECTED
    signal.validated_by = reviewer
    signal.validated_at = timezone.now()
    signal.validation_notes = notes.strip()

    signal.full_clean()
    signal.save()

    record_signal_history(
        signal=signal,
        from_status=previous_status,
        to_status=Signal.Status.CORRECTED,
        changed_by=reviewer,
        reason=notes,
        metadata={
            "action": "correct",
            "before_data": before_data,
            "after_data": signal_snapshot(signal),
        },
    )

    return signal


@transaction.atomic
def reject_signal(
    *,
    signal: Signal,
    reviewer,
    notes: str,
) -> Signal:
    validate_active_user(
        reviewer,
        "Reviewer",
    )

    if signal.status not in {
        Signal.Status.DRAFT,
        Signal.Status.NEEDS_REVIEW,
        Signal.Status.UNDER_REVIEW,
    }:
        raise ValidationError(
            "Sinyal pada status ini tidak dapat ditolak."
        )

    if not notes.strip():
        raise ValidationError(
            "Alasan penolakan sinyal wajib diisi."
        )

    previous_status = signal.status

    signal.status = Signal.Status.REJECTED
    signal.validated_by = reviewer
    signal.validated_at = timezone.now()
    signal.validation_notes = notes.strip()

    signal.save(
        update_fields=[
            "status",
            "validated_by",
            "validated_at",
            "validation_notes",
            "updated_at",
            "last_updated_at",
        ]
    )

    record_signal_history(
        signal=signal,
        from_status=previous_status,
        to_status=Signal.Status.REJECTED,
        changed_by=reviewer,
        reason=notes,
    )

    return signal


@transaction.atomic
def escalate_signal(
    *,
    signal: Signal,
    reviewer,
    notes: str,
    priority_level: str | None = None,
) -> Signal:
    validate_active_user(
        reviewer,
        "Reviewer",
    )

    validate_transition(
        signal=signal,
        target_status=Signal.Status.ESCALATED,
    )

    if not notes.strip():
        raise ValidationError(
            "Alasan eskalasi sinyal wajib diisi."
        )

    if not signal.analyst_judgement.strip():
        raise ValidationError(
            "Sinyal belum memiliki judgement analis."
        )

    previous_status = signal.status

    signal.status = Signal.Status.ESCALATED

    if priority_level:
        signal.priority_level = priority_level

    signal.save(
        update_fields=[
            "status",
            "priority_level",
            "updated_at",
            "last_updated_at",
        ]
    )

    record_signal_history(
        signal=signal,
        from_status=previous_status,
        to_status=Signal.Status.ESCALATED,
        changed_by=reviewer,
        reason=notes,
        metadata={
            "priority_level": signal.priority_level,
        },
    )

    from apps.notifications.services import notify_users
    from apps.notifications.models import Notification
    from django.urls import reverse

    notify_users(
        notification_type=Notification.NotificationType.SIGNAL_ESCALATED,
        title=f"Sinyal dieskalasi: {signal.title}",
        body=f"Alasan eskalasi: {notes.strip()[:200]}",
        link_url=(
            reverse("dashboard:signal-workspace")
            + f"?signal={signal.id}"
        ),
        exclude_user=reviewer,
    )

    return signal


@transaction.atomic
def close_signal(
    *,
    signal: Signal,
    reviewer,
    notes: str,
) -> Signal:
    validate_active_user(
        reviewer,
        "Reviewer",
    )

    validate_transition(
        signal=signal,
        target_status=Signal.Status.CLOSED,
    )

    if not notes.strip():
        raise ValidationError(
            "Alasan penutupan sinyal wajib diisi."
        )

    previous_status = signal.status

    signal.status = Signal.Status.CLOSED
    signal.closed_at = timezone.now()

    signal.save(
        update_fields=[
            "status",
            "closed_at",
            "updated_at",
            "last_updated_at",
        ]
    )

    record_signal_history(
        signal=signal,
        from_status=previous_status,
        to_status=Signal.Status.CLOSED,
        changed_by=reviewer,
        reason=notes,
    )

    return signal
