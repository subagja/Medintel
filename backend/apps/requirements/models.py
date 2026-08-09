import uuid

from django.conf import settings
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models
from django.db.models import Q

from apps.entities.models import Disease
from apps.locations.models import Location
from apps.indicators.models import Indicator


class IntelligenceRequirement(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draf"
        ACTIVE = "active", "Aktif"
        ANSWERED = "answered", "Terjawab"
        CLOSED = "closed", "Ditutup"

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

    question = models.TextField(
        default="",
        help_text="Pertanyaan utama yang harus dijawab oleh proses intelijen.",
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

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
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

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="assigned_intelligence_requirements",
        null=True,
        blank=True,
    )

    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="activated_intelligence_requirements",
        null=True,
        blank=True,
    )

    activated_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    answer_summary = models.TextField(
        blank=True,
        help_text="Simpulan analitis yang menjawab pertanyaan utama.",
    )

    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="answered_intelligence_requirements",
        null=True,
        blank=True,
    )

    answered_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    closure_notes = models.TextField(blank=True)

    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="closed_intelligence_requirements",
        null=True,
        blank=True,
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

    collection_sessions = models.ManyToManyField(
        "collection.CollectionSession",
        through="RequirementCollectionSession",
        related_name="intelligence_requirements",
        blank=True,
    )

    articles = models.ManyToManyField(
        "articles.Article",
        through="RequirementArticle",
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
                fields=["status", "priority", "updated_at"],
                name="intelreq_status_priority_idx",
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

    @property
    def is_editable(self) -> bool:
        return self.status in {self.Status.DRAFT, self.Status.ACTIVE}


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


class RequirementCollectionSession(models.Model):
    requirement = models.ForeignKey(
        IntelligenceRequirement,
        on_delete=models.CASCADE,
        related_name="collection_links",
    )
    session = models.ForeignKey(
        "collection.CollectionSession",
        on_delete=models.CASCADE,
        related_name="requirement_links",
    )
    notes = models.TextField(blank=True)
    linked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="linked_requirement_collection_sessions",
        null=True,
        blank=True,
    )
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-linked_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["requirement", "session"],
                name="unique_requirement_collection_session",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.requirement.code} — {self.session.reference}"


class RequirementArticle(models.Model):
    class LinkSource(models.TextChoices):
        COLLECTION = "collection", "Sesi Koleksi"
        ANALYST = "analyst", "Analis"

    requirement = models.ForeignKey(
        IntelligenceRequirement,
        on_delete=models.CASCADE,
        related_name="article_links",
    )
    article = models.ForeignKey(
        "articles.Article",
        on_delete=models.PROTECT,
        related_name="requirement_links",
    )
    relevance_score = models.FloatField(
        default=1.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    relevance_reason = models.TextField(blank=True)
    link_source = models.CharField(
        max_length=20,
        choices=LinkSource.choices,
        default=LinkSource.ANALYST,
    )
    linked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="linked_requirement_articles",
        null=True,
        blank=True,
    )
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-linked_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["requirement", "article"],
                name="unique_requirement_article",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.requirement.code} — {self.article.title}"


class RequirementInformationGap(models.Model):
    class Priority(models.TextChoices):
        LOW = "low", "Rendah"
        MEDIUM = "medium", "Sedang"
        HIGH = "high", "Tinggi"

    class Status(models.TextChoices):
        OPEN = "open", "Terbuka"
        RESOLVED = "resolved", "Terpenuhi"
        CANCELLED = "cancelled", "Dibatalkan"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    requirement = models.ForeignKey(
        IntelligenceRequirement,
        on_delete=models.CASCADE,
        related_name="information_gaps",
    )
    description = models.TextField()
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.MEDIUM,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    resolution_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_requirement_information_gaps",
        null=True,
        blank=True,
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="resolved_requirement_information_gaps",
        null=True,
        blank=True,
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "-priority", "created_at"]

    def __str__(self) -> str:
        return f"{self.requirement.code} — {self.description[:80]}"


class RequirementHistory(models.Model):
    class Action(models.TextChoices):
        CREATED = "created", "Dibuat"
        UPDATED = "updated", "Diperbarui"
        ACTIVATED = "activated", "Diaktifkan"
        ANSWERED = "answered", "Dinyatakan Terjawab"
        CLOSED = "closed", "Ditutup"
        COLLECTION_LINKED = "collection_linked", "Sesi Koleksi Ditautkan"
        ARTICLE_LINKED = "article_linked", "Artikel Ditautkan"
        ARTICLE_UNLINKED = "article_unlinked", "Tautan Artikel Dilepas"
        GAP_OPENED = "gap_opened", "Kesenjangan Dibuka"
        GAP_RESOLVED = "gap_resolved", "Kesenjangan Dipenuhi"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    requirement = models.ForeignKey(
        IntelligenceRequirement,
        on_delete=models.CASCADE,
        related_name="history",
    )
    action = models.CharField(
        max_length=30,
        choices=Action.choices,
        db_index=True,
    )
    from_status = models.CharField(
        max_length=20,
        choices=IntelligenceRequirement.Status.choices,
        blank=True,
    )
    to_status = models.CharField(
        max_length=20,
        choices=IntelligenceRequirement.Status.choices,
    )
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="intelligence_requirement_history",
        null=True,
        blank=True,
    )
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self) -> str:
        return f"{self.requirement.code} — {self.get_action_display()}"
