# from typing import Any

# from django.core.exceptions import ValidationError
# from django.db import transaction
# from django.utils import timezone

# from apps.entities.models import Disease, Location

# from ..models import (
#     Indicator,
#     IndicatorReviewLog,
#     IndicatorType,
# )

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import (
    Indicator,
    IndicatorReviewLog,
)


INDICATOR_CORRECTION_FIELDS = {
    "indicator_type",
    "disease",
    "location",
    "event_date",
    "value",
    "unit",
    "direction",
    "summary",
    "confidence_score",
}


def serialize_value(value: Any) -> Any:
    if value is None:
        return None

    if hasattr(value, "isoformat"):
        return value.isoformat()

    if hasattr(value, "pk"):
        return str(value.pk)

    return value


def indicator_snapshot(
    indicator: Indicator,
) -> dict[str, Any]:
    return {
        "indicator_type_id": str(
            indicator.indicator_type_id
        ),
        "disease_id": (
            str(indicator.disease_id)
            if indicator.disease_id
            else None
        ),
        "location_id": (
            str(indicator.location_id)
            if indicator.location_id
            else None
        ),
        "event_date": serialize_value(
            indicator.event_date
        ),
        "value": indicator.value,
        "unit": indicator.unit,
        "direction": indicator.direction,
        "summary": indicator.summary,
        "confidence_score": indicator.confidence_score,
        "status": indicator.status,
        "created_by_system": indicator.created_by_system,
        "validated_by_id": (
            str(indicator.validated_by_id)
            if indicator.validated_by_id
            else None
        ),
        "validated_at": (
            indicator.validated_at.isoformat()
            if indicator.validated_at
            else None
        ),
        "validation_notes": indicator.validation_notes,
    }


def create_indicator_review_log(
    *,
    indicator: Indicator,
    action: str,
    reviewer,
    before_data: dict,
    after_data: dict,
    notes: str,
) -> IndicatorReviewLog:
    return IndicatorReviewLog.objects.create(
        indicator=indicator,
        action=action,
        reviewer=reviewer,
        before_data=before_data,
        after_data=after_data,
        notes=notes.strip(),
    )


def validate_reviewer(reviewer) -> None:
    if reviewer is None:
        raise ValidationError(
            "Reviewer wajib ditentukan."
        )

    if not getattr(reviewer, "is_active", False):
        raise ValidationError(
            "Reviewer tidak aktif."
        )


@transaction.atomic
def validate_indicator(
    *,
    indicator: Indicator,
    reviewer,
    notes: str = "",
) -> Indicator:
    validate_reviewer(reviewer)

    if indicator.status == Indicator.Status.REJECTED:
        raise ValidationError(
            "Indikator yang telah ditolak tidak dapat langsung "
            "divalidasi. Lakukan koreksi terlebih dahulu."
        )

    before_data = indicator_snapshot(indicator)

    indicator.status = Indicator.Status.VALIDATED
    indicator.validated_by = reviewer
    indicator.validated_at = timezone.now()
    indicator.validation_notes = notes.strip()

    indicator.full_clean()
    indicator.save(
        update_fields=[
            "status",
            "validated_by",
            "validated_at",
            "validation_notes",
            "updated_at",
        ]
    )

    create_indicator_review_log(
        indicator=indicator,
        action=IndicatorReviewLog.Action.VALIDATE,
        reviewer=reviewer,
        before_data=before_data,
        after_data=indicator_snapshot(indicator),
        notes=notes,
    )

    return indicator


@transaction.atomic
def correct_indicator(
    *,
    indicator: Indicator,
    reviewer,
    corrections: dict[str, Any],
    notes: str,
) -> Indicator:
    validate_reviewer(reviewer)

    if not corrections:
        raise ValidationError(
            "Data koreksi indikator tidak boleh kosong."
        )

    if not notes.strip():
        raise ValidationError(
            "Alasan koreksi indikator wajib diisi."
        )

    unknown_fields = (
        set(corrections)
        - INDICATOR_CORRECTION_FIELDS
    )

    if unknown_fields:
        raise ValidationError(
            "Field koreksi tidak diperbolehkan: "
            + ", ".join(sorted(unknown_fields))
        )

    before_data = indicator_snapshot(indicator)

    for field_name, value in corrections.items():
        setattr(
            indicator,
            field_name,
            value,
        )

    indicator.status = Indicator.Status.CORRECTED
    indicator.validated_by = reviewer
    indicator.validated_at = timezone.now()
    indicator.validation_notes = notes.strip()

    indicator.full_clean()
    indicator.save()

    create_indicator_review_log(
        indicator=indicator,
        action=IndicatorReviewLog.Action.CORRECT,
        reviewer=reviewer,
        before_data=before_data,
        after_data=indicator_snapshot(indicator),
        notes=notes,
    )

    return indicator


@transaction.atomic
def reject_indicator(
    *,
    indicator: Indicator,
    reviewer,
    notes: str,
) -> Indicator:
    validate_reviewer(reviewer)

    if not notes.strip():
        raise ValidationError(
            "Alasan penolakan indikator wajib diisi."
        )

    before_data = indicator_snapshot(indicator)

    indicator.status = Indicator.Status.REJECTED
    indicator.validated_by = reviewer
    indicator.validated_at = timezone.now()
    indicator.validation_notes = notes.strip()

    indicator.save(
        update_fields=[
            "status",
            "validated_by",
            "validated_at",
            "validation_notes",
            "updated_at",
        ]
    )

    create_indicator_review_log(
        indicator=indicator,
        action=IndicatorReviewLog.Action.REJECT,
        reviewer=reviewer,
        before_data=before_data,
        after_data=indicator_snapshot(indicator),
        notes=notes,
    )

    return indicator


def indicator_is_eligible_for_signal(
    indicator: Indicator,
) -> tuple[bool, str]:
    eligible_statuses = {
        Indicator.Status.VALIDATED,
        Indicator.Status.CORRECTED,
    }

    if indicator.status not in eligible_statuses:
        return (
            False,
            "Indikator belum divalidasi atau dikoreksi analis.",
        )

    if not indicator.disease:
        return (
            False,
            "Indikator tidak memiliki penyakit.",
        )

    if not indicator.location:
        return (
            False,
            "Indikator tidak memiliki lokasi.",
        )

    if not indicator.evidences.exists():
        return (
            False,
            "Indikator tidak memiliki bukti pendukung.",
        )

    return True, ""