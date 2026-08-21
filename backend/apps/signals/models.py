import uuid

from django.conf import settings
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models

from apps.articles.models import Article
from apps.entities.models import Disease
from apps.locations.models import Location
from apps.indicators.models import Indicator
from apps.requirements.models import IntelligenceRequirement


class Signal(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draf"
        NEEDS_REVIEW = "needs_review", "Perlu Ditinjau"
        UNDER_REVIEW = "under_review", "Sedang Ditinjau"
        VALIDATED = "validated", "Tervalidasi"
        CORRECTED = "corrected", "Dikoreksi"
        REJECTED = "rejected", "Ditolak"
        ESCALATED = "escalated", "Dieskalasi"
        CLOSED = "closed", "Ditutup"

    class PriorityLevel(models.TextChoices):
        LOW = "low", "Rendah"
        MEDIUM = "medium", "Sedang"
        HIGH = "high", "Tinggi"
        CRITICAL = "critical", "Kritis"

    class ConfidenceLevel(models.TextChoices):
        LOW = "low", "Rendah"
        MEDIUM = "medium", "Sedang"
        HIGH = "high", "Tinggi"
        UNASSESSED = "unassessed", "Belum Dinilai"

    class EventClassification(models.TextChoices):
        UNDETERMINED = "undetermined", "Belum ditentukan"
        ROUTINE = "routine", "Endemik / rutin"
        EMERGING = "emerging", "Emerging"
        RE_EMERGING = "re_emerging", "Re-emerging"
        UNKNOWN_CLUSTER = "unknown_cluster", "Klaster belum diketahui"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    code = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
    )

    title = models.CharField(
        max_length=500,
    )

    summary = models.TextField()

    primary_disease = models.ForeignKey(
        Disease,
        on_delete=models.PROTECT,
        related_name="primary_signals",
    )

    primary_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="primary_signals",
    )

    event_start_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
    )

    event_end_date = models.DateField(
        null=True,
        blank=True,
    )

    first_detected_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    last_updated_at = models.DateTimeField(
        auto_now=True,
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NEEDS_REVIEW,
        db_index=True,
    )

    priority_level = models.CharField(
        max_length=20,
        choices=PriorityLevel.choices,
        default=PriorityLevel.MEDIUM,
        db_index=True,
    )

    confidence_level = models.CharField(
        max_length=20,
        choices=ConfidenceLevel.choices,
        default=ConfidenceLevel.UNASSESSED,
        db_index=True,
    )

    event_classification = models.CharField(
        max_length=30,
        choices=EventClassification.choices,
        default=EventClassification.UNDETERMINED,
        db_index=True,
        help_text=(
            "Klasifikasi kejadian pada lokasi dan periode sinyal; "
            "bukan atribut permanen penyakit."
        ),
    )

    classification_basis = models.TextField(
        blank=True,
        help_text="Bukti epidemiologis yang mendasari klasifikasi kejadian.",
    )

    system_score = models.FloatField(
        null=True,
        blank=True,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(1.0),
        ],
        help_text="Skor teknis awal pembentukan sinyal.",
    )

    created_by_system = models.BooleanField(
        default=True,
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="assigned_signals",
        null=True,
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_signals",
        null=True,
        blank=True,
    )

    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="validated_signals",
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

    analyst_judgement = models.TextField(
        blank=True,
        help_text="Penilaian atau judgement awal analis terhadap sinyal.",
    )

    implication = models.TextField(
        blank=True,
        help_text="Implikasi yang mungkin timbul dari sinyal.",
    )

    recommended_action = models.TextField(
        blank=True,
        help_text="Rekomendasi tindak lanjut awal.",
    )

    information_gaps = models.TextField(
        blank=True,
        help_text="Informasi yang masih belum tersedia atau perlu diverifikasi.",
    )

    closed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    indicators = models.ManyToManyField(
        Indicator,
        through="SignalIndicator",
        related_name="signals",
        blank=True,
    )

    articles = models.ManyToManyField(
        Article,
        through="SignalArticle",
        related_name="signals",
        blank=True,
    )

    requirements = models.ManyToManyField(
        IntelligenceRequirement,
        through="SignalRequirement",
        related_name="signals",
        blank=True,
    )

    diseases = models.ManyToManyField(
        Disease,
        through="SignalDisease",
        related_name="signals",
        blank=True,
    )

    locations = models.ManyToManyField(
        Location,
        through="SignalLocation",
        related_name="signals",
        blank=True,
    )

    class Meta:
        ordering = [
            "-priority_level",
            "-last_updated_at",
        ]
        indexes = [
            models.Index(
                fields=[
                    "status",
                    "priority_level",
                    "last_updated_at",
                ],
                name="signal_status_priority_idx",
            ),
            models.Index(
                fields=[
                    "primary_disease",
                    "primary_location",
                    "event_start_date",
                ],
                name="signal_dis_loc_date_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.title}"


class SignalIndicator(models.Model):
    signal = models.ForeignKey(
        Signal,
        on_delete=models.CASCADE,
        related_name="signal_indicators",
    )

    indicator = models.ForeignKey(
        Indicator,
        on_delete=models.PROTECT,
        related_name="signal_links",
    )

    importance_score = models.FloatField(
        default=1.0,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(10.0),
        ],
    )

    is_primary = models.BooleanField(
        default=False,
    )

    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="added_signal_indicators",
        null=True,
        blank=True,
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["signal", "indicator"],
                name="unique_signal_indicator",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.signal.code} — "
            f"{self.indicator.indicator_type.code}"
        )


class SignalArticle(models.Model):
    class SupportType(models.TextChoices):
        PRIMARY = "primary", "Sumber Utama"
        SUPPORTING = "supporting", "Sumber Pendukung"
        CORROBORATING = "corroborating", "Sumber Penguat"
        CONTRADICTING = "contradicting", "Sumber Bertentangan"

    signal = models.ForeignKey(
        Signal,
        on_delete=models.CASCADE,
        related_name="signal_articles",
    )

    article = models.ForeignKey(
        Article,
        on_delete=models.PROTECT,
        related_name="signal_links",
    )

    support_type = models.CharField(
        max_length=20,
        choices=SupportType.choices,
        default=SupportType.SUPPORTING,
        db_index=True,
    )

    relevance_score = models.FloatField(
        null=True,
        blank=True,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(1.0),
        ],
    )

    is_primary_source = models.BooleanField(
        default=False,
    )

    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="added_signal_articles",
        null=True,
        blank=True,
    )

    added_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["signal", "article"],
                name="unique_signal_article",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.signal.code} — {self.article.title}"


class SignalRequirement(models.Model):
    signal = models.ForeignKey(
        Signal,
        on_delete=models.CASCADE,
        related_name="signal_requirements",
    )

    requirement = models.ForeignKey(
        IntelligenceRequirement,
        on_delete=models.PROTECT,
        related_name="signal_links",
    )

    relevance_score = models.FloatField(
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(1.0),
        ],
    )

    relevance_reason = models.TextField(
        blank=True,
    )

    is_primary = models.BooleanField(
        default=False,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["signal", "requirement"],
                name="unique_signal_requirement",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.signal.code} — {self.requirement.code}"


class SignalDisease(models.Model):
    signal = models.ForeignKey(
        Signal,
        on_delete=models.CASCADE,
        related_name="signal_diseases",
    )

    disease = models.ForeignKey(
        Disease,
        on_delete=models.PROTECT,
        related_name="signal_links",
    )

    is_primary = models.BooleanField(
        default=False,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["signal", "disease"],
                name="unique_signal_disease",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.signal.code} — {self.disease.name}"


class SignalLocation(models.Model):
    signal = models.ForeignKey(
        Signal,
        on_delete=models.CASCADE,
        related_name="signal_locations",
    )

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="signal_links",
    )

    is_primary = models.BooleanField(
        default=False,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["signal", "location"],
                name="unique_signal_location",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.signal.code} — {self.location.name}"


class SignalHistory(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    signal = models.ForeignKey(
        Signal,
        on_delete=models.CASCADE,
        related_name="histories",
    )

    from_status = models.CharField(
        max_length=20,
        choices=Signal.Status.choices,
        blank=True,
    )

    to_status = models.CharField(
        max_length=20,
        choices=Signal.Status.choices,
    )

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="signal_status_changes",
        null=True,
        blank=True,
    )

    reason = models.TextField(
        blank=True,
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    changed_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    class Meta:
        ordering = ["-changed_at"]
        indexes = [
            models.Index(
                fields=["signal", "changed_at"],
                name="signalhistory_signal_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.signal.code}: "
            f"{self.from_status or '-'} â†’ {self.to_status}"
        )
