import uuid

from django.conf import settings
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models
from django.db.models import Q

from apps.entities.models import Disease, Location
from apps.indicators.models import Indicator


class IntelligenceRequirement(models.Model):
    class Priority(models.TextChoices):
        LOW = "low", "Rendah"
        MEDIUM = "medium", "Sedang"
        HIGH = "high", "Tinggi"
        CRITICAL = "critical", "Kritis"

    class RequirementType(models.TextChoices):
        DISEASE_EVENT = (
            "disease_event",
            "Kejadian Penyakit",
        )
        GEOGRAPHIC_SPREAD = (
            "geographic_spread",
            "Penyebaran Geografis",
        )
        CROSS_BORDER = (
            "cross_border",
            "Ancaman Lintas Negara",
        )
        IMPACT = (
            "impact",
            "Dampak Kesehatan",
        )
        ANOMALY = (
            "anomaly",
            "Kejadian Tidak Lazim",
        )
        RESPONSE = (
            "response",
            "Respons Pemerintah",
        )
        OTHER = "other", "Lainnya"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    code = models.SlugField(
        max_length=50,
        unique=True,
        help_text="Contoh: IR-001 atau peningkatan-klb.",
    )

    title = models.CharField(
        max_length=300,
    )

    description = models.TextField()

    requirement_type = models.CharField(
        max_length=30,
        choices=RequirementType.choices,
        db_index=True,
    )

    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.MEDIUM,
        db_index=True,
    )

    valid_from = models.DateField(
        null=True,
        blank=True,
    )

    valid_until = models.DateField(
        null=True,
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_intelligence_requirements",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    diseases = models.ManyToManyField(
        Disease,
        through="RequirementDisease",
        related_name="intelligence_requirements",
        blank=True,
    )

    locations = models.ManyToManyField(
        Location,
        through="RequirementLocation",
        related_name="intelligence_requirements",
        blank=True,
    )

    indicators = models.ManyToManyField(
        Indicator,
        through="RequirementIndicator",
        related_name="intelligence_requirements",
        blank=True,
    )

    class Meta:
        ordering = [
            "-priority",
            "code",
        ]
        indexes = [
            models.Index(
                fields=["is_active", "priority"],
                name="intelreq_active_priority_idx",
            ),
            models.Index(
                fields=[
                    "requirement_type",
                    "is_active",
                ],
                name="intelreq_type_active_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(valid_until__isnull=True)
                    | Q(valid_from__isnull=True)
                    | Q(valid_until__gte=models.F("valid_from"))
                ),
                name="requirement_valid_date_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.title}"


class RequirementKeyword(models.Model):
    class KeywordType(models.TextChoices):
        DISEASE = "disease", "Penyakit"
        TREND = "trend", "Tren"
        IMPACT = "impact", "Dampak"
        LOCATION = "location", "Lokasi"
        RESPONSE = "response", "Respons"
        ANOMALY = "anomaly", "Anomali"
        GENERAL = "general", "Umum"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    requirement = models.ForeignKey(
        IntelligenceRequirement,
        on_delete=models.CASCADE,
        related_name="keywords",
    )

    keyword = models.CharField(
        max_length=200,
    )

    keyword_type = models.CharField(
        max_length=20,
        choices=KeywordType.choices,
        default=KeywordType.GENERAL,
        db_index=True,
    )

    weight = models.FloatField(
        default=1.0,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(10.0),
        ],
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = [
            "keyword_type",
            "keyword",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "requirement",
                    "keyword",
                ],
                name="unique_requirement_keyword",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.requirement.code} — "
            f"{self.keyword}"
        )


class RequirementDisease(models.Model):
    requirement = models.ForeignKey(
        IntelligenceRequirement,
        on_delete=models.CASCADE,
        related_name="requirement_diseases",
    )

    disease = models.ForeignKey(
        Disease,
        on_delete=models.PROTECT,
        related_name="requirement_links",
    )

    priority_weight = models.FloatField(
        default=1.0,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(10.0),
        ],
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
                fields=[
                    "requirement",
                    "disease",
                ],
                name="unique_requirement_disease",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.requirement.code} — "
            f"{self.disease.name}"
        )


class RequirementLocation(models.Model):
    requirement = models.ForeignKey(
        IntelligenceRequirement,
        on_delete=models.CASCADE,
        related_name="requirement_locations",
    )

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="requirement_links",
    )

    priority_weight = models.FloatField(
        default=1.0,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(10.0),
        ],
    )

    include_descendants = models.BooleanField(
        default=True,
        help_text=(
            "Apabila aktif, wilayah di bawah lokasi ini "
            "juga dianggap relevan."
        ),
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
                fields=[
                    "requirement",
                    "location",
                ],
                name="unique_requirement_location",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.requirement.code} — "
            f"{self.location.name}"
        )


class RequirementIndicator(models.Model):
    class MatchSource(models.TextChoices):
        SYSTEM = "system", "Sistem"
        ANALYST = "analyst", "Analis"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    requirement = models.ForeignKey(
        IntelligenceRequirement,
        on_delete=models.CASCADE,
        related_name="indicator_matches",
    )

    indicator = models.ForeignKey(
        Indicator,
        on_delete=models.CASCADE,
        related_name="requirement_matches",
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

    matched_by = models.CharField(
        max_length=20,
        choices=MatchSource.choices,
        default=MatchSource.SYSTEM,
    )

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="reviewed_requirement_matches",
        null=True,
        blank=True,
    )

    reviewed_at = models.DateTimeField(
        null=True,
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
            "-relevance_score",
            "-created_at",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "requirement",
                    "indicator",
                ],
                name="unique_requirement_indicator",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "requirement",
                    "relevance_score",
                ],
                name="reqindicator_score_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.requirement.code} — "
            f"{self.indicator.indicator_type.code}"
        )