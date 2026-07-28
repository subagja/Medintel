import uuid

from django.conf import settings
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models

from apps.articles.models import Article
from apps.entities.models import (
    ArticleFact,
    Disease,
    Location,
)


class IndicatorType(models.Model):
    class Category(models.TextChoices):
        EPIDEMIOLOGICAL = (
            "epidemiological",
            "Epidemiologis",
        )
        GEOGRAPHIC = (
            "geographic",
            "Geografis",
        )
        IMPACT = (
            "impact",
            "Dampak",
        )
        RESPONSE = (
            "response",
            "Respons",
        )
        INFORMATION = (
            "information",
            "Informasi",
        )
        ANOMALY = (
            "anomaly",
            "Anomali",
        )

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    code = models.SlugField(
        max_length=100,
        unique=True,
    )

    name = models.CharField(
        max_length=200,
        unique=True,
    )

    description = models.TextField(
        blank=True,
    )

    category = models.CharField(
        max_length=30,
        choices=Category.choices,
        db_index=True,
    )

    default_weight = models.FloatField(
        default=1.0,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(10.0),
        ],
    )

    requires_analyst_validation = models.BooleanField(
        default=True,
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "category",
            "name",
        ]

    def __str__(self) -> str:
        return self.name


class Indicator(models.Model):
    class Status(models.TextChoices):
        DETECTED = "detected", "Terdeteksi"
        NEEDS_REVIEW = "needs_review", "Perlu Ditinjau"
        VALIDATED = "validated", "Tervalidasi"
        CORRECTED = "corrected", "Dikoreksi"
        REJECTED = "rejected", "Ditolak"

    class Direction(models.TextChoices):
        INCREASING = "increasing", "Meningkat"
        DECREASING = "decreasing", "Menurun"
        STABLE = "stable", "Stabil"
        SPREADING = "spreading", "Meluas"
        NEW = "new", "Baru"
        UNKNOWN = "unknown", "Belum Diketahui"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    indicator_type = models.ForeignKey(
        IndicatorType,
        on_delete=models.PROTECT,
        related_name="indicators",
    )

    disease = models.ForeignKey(
        Disease,
        on_delete=models.PROTECT,
        related_name="indicators",
        null=True,
        blank=True,
    )

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="indicators",
        null=True,
        blank=True,
    )

    event_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
    )

    value = models.FloatField(
        null=True,
        blank=True,
    )

    unit = models.CharField(
        max_length=50,
        blank=True,
    )

    direction = models.CharField(
        max_length=20,
        choices=Direction.choices,
        default=Direction.UNKNOWN,
        db_index=True,
    )

    summary = models.TextField()

    confidence_score = models.FloatField(
        null=True,
        blank=True,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(1.0),
        ],
        help_text=(
            "Keyakinan teknis sistem terhadap pembentukan indikator."
        ),
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NEEDS_REVIEW,
        db_index=True,
    )

    created_by_system = models.BooleanField(
        default=True,
    )

    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="validated_indicators",
        null=True,
        blank=True,
    )

    validated_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    validation_notes = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "-event_date",
            "-created_at",
        ]
        indexes = [
            models.Index(
                fields=[
                    "indicator_type",
                    "status",
                    "event_date",
                ],
                name="indicator_type_status_idx",
            ),
            models.Index(
                fields=[
                    "disease",
                    "location",
                    "event_date",
                ],
                name="indicator_dis_loc_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.indicator_type.code} - "
            f"{self.summary[:80]}"
        )


class IndicatorEvidence(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    indicator = models.ForeignKey(
        Indicator,
        on_delete=models.CASCADE,
        related_name="evidences",
    )

    article = models.ForeignKey(
        Article,
        on_delete=models.PROTECT,
        related_name="indicator_evidences",
    )

    article_fact = models.ForeignKey(
        ArticleFact,
        on_delete=models.PROTECT,
        related_name="indicator_evidences",
        null=True,
        blank=True,
    )

    evidence_text = models.TextField(
        blank=True,
    )

    confidence_score = models.FloatField(
        null=True,
        blank=True,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(1.0),
        ],
    )

    is_primary_evidence = models.BooleanField(
        default=False,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = [
            "-is_primary_evidence",
            "created_at",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "indicator",
                    "article_fact",
                ],
                name="unique_indicator_article_fact",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "indicator",
                    "is_primary_evidence",
                ],
                name="indicator_evidence_primary_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.indicator.indicator_type.code} - "
            f"{self.article.title}"
        )


class IndicatorReviewLog(models.Model):
    class Action(models.TextChoices):
        VALIDATE = "validate", "Validasi"
        CORRECT = "correct", "Koreksi"
        REJECT = "reject", "Penolakan"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    indicator = models.ForeignKey(
        Indicator,
        on_delete=models.CASCADE,
        related_name="review_logs",
    )

    action = models.CharField(
        max_length=20,
        choices=Action.choices,
        db_index=True,
    )

    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="indicator_reviews",
        null=True,
        blank=True,
    )

    before_data = models.JSONField(
        default=dict,
        blank=True,
    )

    after_data = models.JSONField(
        default=dict,
        blank=True,
    )

    notes = models.TextField(
        blank=True,
    )

    reviewed_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    class Meta:
        ordering = ["-reviewed_at"]
        indexes = [
            models.Index(
                fields=["indicator", "reviewed_at"],
                name="indreview_indicator_idx",
            ),
            models.Index(
                fields=["reviewer", "reviewed_at"],
                name="indreview_reviewer_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.indicator.indicator_type.code} - "
            f"{self.get_action_display()}"
        )