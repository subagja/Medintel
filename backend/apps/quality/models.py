import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class QualityMetricSnapshot(models.Model):
    """Pembekuan metrik agar hasil evaluasi per periode dapat direproduksi."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    title = models.CharField(max_length=300)
    period_start = models.DateField(db_index=True)
    period_end = models.DateField(db_index=True)
    metrics = models.JSONField(default=dict)
    narrative = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="quality_metric_snapshots",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(period_end__gte=models.F("period_start")),
                name="quality_snapshot_valid_period",
            )
        ]

    def __str__(self):
        return f"{self.code} — {self.title}"


class EvaluationRecord(models.Model):
    class EvaluationType(models.TextChoices):
        EXPERT = "expert", "Validasi Ahli"
        UAT = "uat", "User Acceptance Test"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draf"
        COMPLETED = "completed", "Selesai"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    evaluation_type = models.CharField(
        max_length=20,
        choices=EvaluationType.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    evaluator_name = models.CharField(max_length=255)
    evaluator_role = models.CharField(max_length=255)
    institution = models.CharField(max_length=255, blank=True)
    evaluation_date = models.DateField(db_index=True)
    scores = models.JSONField(default=dict, blank=True)
    criterion_notes = models.JSONField(default=dict, blank=True)
    task_results = models.JSONField(default=dict, blank=True)
    general_findings = models.TextField(blank=True)
    recommendations = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_quality_evaluations",
        null=True,
        blank=True,
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="completed_quality_evaluations",
        null=True,
        blank=True,
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-evaluation_date", "-created_at"]
        indexes = [
            models.Index(
                fields=["evaluation_type", "status", "evaluation_date"],
                name="quality_eval_type_status_idx",
            )
        ]

    @property
    def average_score(self):
        values = [
            float(value)
            for value in (self.scores or {}).values()
            if value not in (None, "")
        ]
        return round(sum(values) / len(values), 2) if values else None

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT

    def __str__(self):
        return f"{self.code} — {self.evaluator_name}"


class EvaluationHistory(models.Model):
    class Action(models.TextChoices):
        CREATED = "created", "Dibuat"
        UPDATED = "updated", "Diperbarui"
        COMPLETED = "completed", "Diselesaikan"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evaluation = models.ForeignKey(
        EvaluationRecord,
        on_delete=models.CASCADE,
        related_name="history",
    )
    action = models.CharField(max_length=20, choices=Action.choices, db_index=True)
    from_status = models.CharField(
        max_length=20,
        choices=EvaluationRecord.Status.choices,
        blank=True,
    )
    to_status = models.CharField(
        max_length=20,
        choices=EvaluationRecord.Status.choices,
    )
    metadata = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="quality_evaluation_history",
        null=True,
        blank=True,
    )
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.evaluation.code} — {self.get_action_display()}"
