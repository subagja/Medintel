from dataclasses import dataclass
from datetime import date
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.locations.models import Location

from ..models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ExtractionMethod,
    ExtractionReviewLog,
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


@dataclass(frozen=True)
class PrimaryLocationCorrectionResult:
    relation: ArticleLocation
    created: bool
    changed: bool
    demoted_count: int
    context_count: int


@dataclass(frozen=True)
class PrimaryDiseaseCorrectionResult:
    relation: ArticleDisease
    created: bool
    changed: bool
    demoted_count: int
    context_count: int


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
def set_primary_article_disease(
    *,
    article,
    disease: Disease,
    reviewer,
    notes: str,
) -> PrimaryDiseaseCorrectionResult:
    """Tetapkan satu penyakit utama tanpa menghapus penyakit konteks."""
    normalized_notes = notes.strip()

    if not normalized_notes:
        raise ValidationError(
            "Dasar penetapan penyakit utama wajib diisi."
        )

    if not (
        reviewer
        and getattr(reviewer, "is_authenticated", False)
    ):
        raise ValidationError(
            "Pengguna harus login untuk menetapkan penyakit utama."
        )

    if not disease.is_active:
        raise ValidationError(
            "Penyakit yang dipilih sudah tidak aktif."
        )

    relations = list(
        ArticleDisease.objects.select_for_update()
        .filter(article=article)
        .select_related("disease")
    )

    selected_relation = next(
        (
            relation
            for relation in relations
            if relation.disease_id == disease.id
        ),
        None,
    )

    created = selected_relation is None

    if created:
        selected_relation = ArticleDisease.objects.create(
            article=article,
            disease=disease,
            mention_text=disease.name,
            confidence_score=1.0,
            extraction_method=ExtractionMethod.MANUAL,
            is_primary=False,
        )
        selected_before = {}
        relations.append(selected_relation)
    else:
        selected_before = article_disease_snapshot(
            selected_relation
        )

    changed = created
    demoted_count = 0
    reviewed_at = timezone.now()

    for relation in relations:
        if relation.id == selected_relation.id:
            continue

        if not relation.is_primary:
            continue

        before_data = article_disease_snapshot(relation)

        relation.is_primary = False
        relation.validation_status = ValidationStatus.CORRECTED
        relation.validation_notes = normalized_notes
        relation.validated_by = reviewer
        relation.validated_at = reviewed_at
        relation.full_clean()
        relation.save(
            update_fields=[
                "is_primary",
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
            action=ExtractionReviewLog.Action.CORRECT,
            reviewer=reviewer,
            before_data=before_data,
            after_data=article_disease_snapshot(relation),
            notes=normalized_notes,
        )

        changed = True
        demoted_count += 1

    selected_needs_update = any(
        [
            created,
            demoted_count > 0,
            not selected_relation.is_primary,
            (
                selected_relation.validation_status
                != ValidationStatus.CORRECTED
            ),
            selected_relation.validation_notes
            != normalized_notes,
            selected_relation.validated_by_id
            != reviewer.pk,
        ]
    )

    if selected_needs_update:
        selected_relation.is_primary = True
        selected_relation.validation_status = (
            ValidationStatus.CORRECTED
        )
        selected_relation.validation_notes = normalized_notes
        selected_relation.validated_by = reviewer
        selected_relation.validated_at = reviewed_at
        selected_relation.full_clean()
        selected_relation.save(
            update_fields=[
                "is_primary",
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
            object_id=selected_relation.id,
            action=ExtractionReviewLog.Action.CORRECT,
            reviewer=reviewer,
            before_data=selected_before,
            after_data=article_disease_snapshot(
                selected_relation
            ),
            notes=normalized_notes,
        )

        changed = True

    context_count = max(len(relations) - 1, 0)

    return PrimaryDiseaseCorrectionResult(
        relation=selected_relation,
        created=created,
        changed=changed,
        demoted_count=demoted_count,
        context_count=context_count,
    )

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
def set_primary_article_location(
    *,
    article,
    location: Location,
    reviewer,
    notes: str,
) -> PrimaryLocationCorrectionResult:
    """
    Tetapkan satu lokasi kejadian utama tanpa menghapus lokasi konteks.

    Perubahan ini bersifat keputusan analis. Semua relasi utama lama
    diturunkan menjadi konteks dan setiap perubahan dicatat pada
    ExtractionReviewLog. Fakta artikel tidak direlokasi otomatis agar
    koreksi lokasi tidak mengubah substansi fakta secara terselubung.
    """
    normalized_notes = notes.strip()

    if not normalized_notes:
        raise ValidationError(
            "Dasar koreksi lokasi utama wajib diisi."
        )

    if not (
        reviewer
        and getattr(reviewer, "is_authenticated", False)
    ):
        raise ValidationError(
            "Pengguna harus login untuk mengoreksi lokasi utama."
        )

    if not location.is_active:
        raise ValidationError(
            "Lokasi yang dipilih sudah tidak aktif."
        )

    relations = list(
        ArticleLocation.objects.select_for_update()
        .filter(article=article)
        .select_related("location")
    )

    selected_relation = next(
        (
            relation
            for relation in relations
            if relation.location_id == location.id
        ),
        None,
    )

    created = selected_relation is None

    if created:
        selected_relation = ArticleLocation.objects.create(
            article=article,
            location=location,
            mention_text=location.name,
            confidence_score=1.0,
            extraction_method=ExtractionMethod.MANUAL,
            is_primary=False,
        )
        selected_before = {}
        relations.append(selected_relation)
    else:
        selected_before = article_location_snapshot(
            selected_relation
        )

    changed = created
    demoted_count = 0
    reviewed_at = timezone.now()

    for relation in relations:
        if relation.id == selected_relation.id:
            continue

        if not relation.is_primary:
            continue

        before_data = article_location_snapshot(relation)

        relation.is_primary = False
        relation.validation_status = ValidationStatus.CORRECTED
        relation.validation_notes = normalized_notes
        relation.validated_by = reviewer
        relation.validated_at = reviewed_at
        relation.full_clean()
        relation.save(
            update_fields=[
                "is_primary",
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
            action=ExtractionReviewLog.Action.CORRECT,
            reviewer=reviewer,
            before_data=before_data,
            after_data=article_location_snapshot(relation),
            notes=normalized_notes,
        )

        changed = True
        demoted_count += 1

    selected_needs_update = any(
        [
            created,
            demoted_count > 0,
            not selected_relation.is_primary,
            (
                selected_relation.validation_status
                != ValidationStatus.CORRECTED
            ),
            selected_relation.validation_notes
            != normalized_notes,
            selected_relation.validated_by_id
            != reviewer.pk,
        ]
    )

    if selected_needs_update:
        selected_relation.is_primary = True
        selected_relation.validation_status = (
            ValidationStatus.CORRECTED
        )
        selected_relation.validation_notes = normalized_notes
        selected_relation.validated_by = reviewer
        selected_relation.validated_at = reviewed_at
        selected_relation.full_clean()
        selected_relation.save(
            update_fields=[
                "is_primary",
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
            object_id=selected_relation.id,
            action=ExtractionReviewLog.Action.CORRECT,
            reviewer=reviewer,
            before_data=selected_before,
            after_data=article_location_snapshot(
                selected_relation
            ),
            notes=normalized_notes,
        )

        changed = True

    context_count = max(len(relations) - 1, 0)

    return PrimaryLocationCorrectionResult(
        relation=selected_relation,
        created=created,
        changed=changed,
        demoted_count=demoted_count,
        context_count=context_count,
    )

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
