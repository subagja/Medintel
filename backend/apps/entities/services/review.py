from datetime import date
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ExtractionReviewLog,
    Location,
    ValidationStatus,
)


FACT_CORRECTION_FIELDS = {
    "disease",
    "location",
    "event_date",
    "case_count",
    "death_count",
    "recovery_count",
    "hospitalized_count",
    "trend",
    "affected_group",
    "government_response",
    "fact_text",
}


def serialize_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, date):
        return value.isoformat()

    if hasattr(value, "pk"):
        return str(value.pk)

    return value


def article_disease_snapshot(
    relation: ArticleDisease,
) -> dict[str, Any]:
    return {
        "article_id": str(relation.article_id),
        "disease_id": str(relation.disease_id),
        "mention_text": relation.mention_text,
        "confidence_score": relation.confidence_score,
        "extraction_method": relation.extraction_method,
        "is_primary": relation.is_primary,
        "validation_status": relation.validation_status,
        "validation_notes": relation.validation_notes,
        "validated_by_id": (
            str(relation.validated_by_id)
            if relation.validated_by_id
            else None
        ),
        "validated_at": (
            relation.validated_at.isoformat()
            if relation.validated_at
            else None
        ),
    }


def article_location_snapshot(
    relation: ArticleLocation,
) -> dict[str, Any]:
    return {
        "article_id": str(relation.article_id),
        "location_id": str(relation.location_id),
        "mention_text": relation.mention_text,
        "confidence_score": relation.confidence_score,
        "extraction_method": relation.extraction_method,
        "is_primary": relation.is_primary,
        "validation_status": relation.validation_status,
        "validation_notes": relation.validation_notes,
        "validated_by_id": (
            str(relation.validated_by_id)
            if relation.validated_by_id
            else None
        ),
        "validated_at": (
            relation.validated_at.isoformat()
            if relation.validated_at
            else None
        ),
    }


def article_fact_snapshot(
    fact: ArticleFact,
) -> dict[str, Any]:
    return {
        "article_id": str(fact.article_id),
        "disease_id": (
            str(fact.disease_id)
            if fact.disease_id
            else None
        ),
        "location_id": (
            str(fact.location_id)
            if fact.location_id
            else None
        ),
        "event_date": serialize_value(fact.event_date),
        "case_count": fact.case_count,
        "death_count": fact.death_count,
        "recovery_count": fact.recovery_count,
        "hospitalized_count": fact.hospitalized_count,
        "trend": fact.trend,
        "affected_group": fact.affected_group,
        "government_response": fact.government_response,
        "fact_text": fact.fact_text,
        "confidence_score": fact.confidence_score,
        "extraction_method": fact.extraction_method,
        "validation_status": fact.validation_status,
        "validation_notes": fact.validation_notes,
        "validated_by_id": (
            str(fact.validated_by_id)
            if fact.validated_by_id
            else None
        ),
        "validated_at": (
            fact.validated_at.isoformat()
            if fact.validated_at
            else None
        ),
    }


def create_review_log(
    *,
    object_type: str,
    object_id,
    action: str,
    reviewer,
    before_data: dict,
    after_data: dict,
    notes: str,
) -> ExtractionReviewLog:
    return ExtractionReviewLog.objects.create(
        object_type=object_type,
        object_id=object_id,
        action=action,
        reviewer=reviewer,
        before_data=before_data,
        after_data=after_data,
        notes=notes,
    )


@transaction.atomic
def validate_article_disease(
    *,
    relation: ArticleDisease,
    reviewer,
    notes: str = "",
) -> ArticleDisease:
    before_data = article_disease_snapshot(relation)

    relation.validation_status = ValidationStatus.VALIDATED
    relation.validation_notes = notes.strip()
    relation.validated_by = reviewer
    relation.validated_at = timezone.now()

    relation.full_clean()
    relation.save(
        update_fields=[
            "validation_status",
            "validation_notes",
            "validated_by",
            "validated_at",
            "updated_at",
        ]
    )

    create_review_log(
        object_type=(
            ExtractionReviewLog.ObjectType.ARTICLE_DISEASE
        ),
        object_id=relation.id,
        action=ExtractionReviewLog.Action.VALIDATE,
        reviewer=reviewer,
        before_data=before_data,
        after_data=article_disease_snapshot(relation),
        notes=notes,
    )

    return relation


@transaction.atomic
def correct_article_disease(
    *,
    relation: ArticleDisease,
    disease: Disease,
    reviewer,
    mention_text: str | None = None,
    is_primary: bool | None = None,
    notes: str = "",
) -> ArticleDisease:
    before_data = article_disease_snapshot(relation)

    relation.disease = disease

    if mention_text is not None:
        relation.mention_text = mention_text.strip()

    if is_primary is not None:
        relation.is_primary = is_primary

    relation.validation_status = ValidationStatus.CORRECTED
    relation.validation_notes = notes.strip()
    relation.validated_by = reviewer
    relation.validated_at = timezone.now()

    relation.full_clean()
    relation.save()

    create_review_log(
        object_type=(
            ExtractionReviewLog.ObjectType.ARTICLE_DISEASE
        ),
        object_id=relation.id,
        action=ExtractionReviewLog.Action.CORRECT,
        reviewer=reviewer,
        before_data=before_data,
        after_data=article_disease_snapshot(relation),
        notes=notes,
    )

    return relation


@transaction.atomic
def reject_article_disease(
    *,
    relation: ArticleDisease,
    reviewer,
    notes: str,
) -> ArticleDisease:
    if not notes.strip():
        raise ValidationError(
            "Alasan penolakan hasil ekstraksi penyakit wajib diisi."
        )

    before_data = article_disease_snapshot(relation)

    relation.validation_status = ValidationStatus.REJECTED
    relation.validation_notes = notes.strip()
    relation.validated_by = reviewer
    relation.validated_at = timezone.now()

    relation.save(
        update_fields=[
            "validation_status",
            "validation_notes",
            "validated_by",
            "validated_at",
            "updated_at",
        ]
    )

    create_review_log(
        object_type=(
            ExtractionReviewLog.ObjectType.ARTICLE_DISEASE
        ),
        object_id=relation.id,
        action=ExtractionReviewLog.Action.REJECT,
        reviewer=reviewer,
        before_data=before_data,
        after_data=article_disease_snapshot(relation),
        notes=notes,
    )

    return relation

@transaction.atomic
def validate_article_location(
    *,
    relation: ArticleLocation,
    reviewer,
    notes: str = "",
) -> ArticleLocation:
    before_data = article_location_snapshot(relation)

    relation.validation_status = ValidationStatus.VALIDATED
    relation.validation_notes = notes.strip()
    relation.validated_by = reviewer
    relation.validated_at = timezone.now()

    relation.full_clean()
    relation.save(
        update_fields=[
            "validation_status",
            "validation_notes",
            "validated_by",
            "validated_at",
            "updated_at",
        ]
    )

    create_review_log(
        object_type=(
            ExtractionReviewLog.ObjectType.ARTICLE_LOCATION
        ),
        object_id=relation.id,
        action=ExtractionReviewLog.Action.VALIDATE,
        reviewer=reviewer,
        before_data=before_data,
        after_data=article_location_snapshot(relation),
        notes=notes,
    )

    return relation


@transaction.atomic
def correct_article_location(
    *,
    relation: ArticleLocation,
    location: Location,
    reviewer,
    mention_text: str | None = None,
    is_primary: bool | None = None,
    notes: str = "",
) -> ArticleLocation:
    before_data = article_location_snapshot(relation)

    relation.location = location

    if mention_text is not None:
        relation.mention_text = mention_text.strip()

    if is_primary is not None:
        relation.is_primary = is_primary

    relation.validation_status = ValidationStatus.CORRECTED
    relation.validation_notes = notes.strip()
    relation.validated_by = reviewer
    relation.validated_at = timezone.now()

    relation.full_clean()
    relation.save()

    create_review_log(
        object_type=(
            ExtractionReviewLog.ObjectType.ARTICLE_LOCATION
        ),
        object_id=relation.id,
        action=ExtractionReviewLog.Action.CORRECT,
        reviewer=reviewer,
        before_data=before_data,
        after_data=article_location_snapshot(relation),
        notes=notes,
    )

    return relation


@transaction.atomic
def reject_article_location(
    *,
    relation: ArticleLocation,
    reviewer,
    notes: str,
) -> ArticleLocation:
    if not notes.strip():
        raise ValidationError(
            "Alasan penolakan hasil ekstraksi lokasi wajib diisi."
        )

    before_data = article_location_snapshot(relation)

    relation.validation_status = ValidationStatus.REJECTED
    relation.validation_notes = notes.strip()
    relation.validated_by = reviewer
    relation.validated_at = timezone.now()

    relation.save(
        update_fields=[
            "validation_status",
            "validation_notes",
            "validated_by",
            "validated_at",
            "updated_at",
        ]
    )

    create_review_log(
        object_type=(
            ExtractionReviewLog.ObjectType.ARTICLE_LOCATION
        ),
        object_id=relation.id,
        action=ExtractionReviewLog.Action.REJECT,
        reviewer=reviewer,
        before_data=before_data,
        after_data=article_location_snapshot(relation),
        notes=notes,
    )

    return relation

@transaction.atomic
def validate_article_fact(
    *,
    fact: ArticleFact,
    reviewer,
    notes: str = "",
) -> ArticleFact:
    before_data = article_fact_snapshot(fact)

    fact.validation_status = ValidationStatus.VALIDATED
    fact.validation_notes = notes.strip()
    fact.validated_by = reviewer
    fact.validated_at = timezone.now()

    fact.full_clean()
    fact.save(
        update_fields=[
            "validation_status",
            "validation_notes",
            "validated_by",
            "validated_at",
            "updated_at",
        ]
    )

    create_review_log(
        object_type=(
            ExtractionReviewLog.ObjectType.ARTICLE_FACT
        ),
        object_id=fact.id,
        action=ExtractionReviewLog.Action.VALIDATE,
        reviewer=reviewer,
        before_data=before_data,
        after_data=article_fact_snapshot(fact),
        notes=notes,
    )

    return fact


@transaction.atomic
def correct_article_fact(
    *,
    fact: ArticleFact,
    reviewer,
    corrections: dict[str, Any],
    notes: str,
) -> ArticleFact:
    if not corrections:
        raise ValidationError(
            "Data koreksi fakta tidak boleh kosong."
        )

    unknown_fields = set(corrections) - FACT_CORRECTION_FIELDS

    if unknown_fields:
        raise ValidationError(
            "Field koreksi tidak diperbolehkan: "
            + ", ".join(sorted(unknown_fields))
        )

    before_data = article_fact_snapshot(fact)

    for field_name, value in corrections.items():
        setattr(fact, field_name, value)

    fact.validation_status = ValidationStatus.CORRECTED
    fact.validation_notes = notes.strip()
    fact.validated_by = reviewer
    fact.validated_at = timezone.now()

    fact.full_clean()
    fact.save()

    create_review_log(
        object_type=(
            ExtractionReviewLog.ObjectType.ARTICLE_FACT
        ),
        object_id=fact.id,
        action=ExtractionReviewLog.Action.CORRECT,
        reviewer=reviewer,
        before_data=before_data,
        after_data=article_fact_snapshot(fact),
        notes=notes,
    )

    return fact


@transaction.atomic
def reject_article_fact(
    *,
    fact: ArticleFact,
    reviewer,
    notes: str,
) -> ArticleFact:
    if not notes.strip():
        raise ValidationError(
            "Alasan penolakan fakta wajib diisi."
        )

    before_data = article_fact_snapshot(fact)

    fact.validation_status = ValidationStatus.REJECTED
    fact.validation_notes = notes.strip()
    fact.validated_by = reviewer
    fact.validated_at = timezone.now()

    fact.save(
        update_fields=[
            "validation_status",
            "validation_notes",
            "validated_by",
            "validated_at",
            "updated_at",
        ]
    )

    create_review_log(
        object_type=(
            ExtractionReviewLog.ObjectType.ARTICLE_FACT
        ),
        object_id=fact.id,
        action=ExtractionReviewLog.Action.REJECT,
        reviewer=reviewer,
        before_data=before_data,
        after_data=article_fact_snapshot(fact),
        notes=notes,
    )

    return fact