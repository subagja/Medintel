import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.articles.models import Article


class ValidationStatus(models.TextChoices):
    UNREVIEWED = "unreviewed", "Belum Ditinjau"
    VALIDATED = "validated", "Tervalidasi"
    CORRECTED = "corrected", "Dikoreksi"
    REJECTED = "rejected", "Ditolak"


class ExtractionMethod(models.TextChoices):
    SYSTEM = "system", "Sistem"
    RULE_BASED = "rule_based", "Rule-Based"
    MODEL = "model", "Model NLP"
    MANUAL = "manual", "Input Manual"


class Disease(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    name = models.CharField(
        max_length=200,
        unique=True,
        help_text="Nama utama penyakit dalam Bahasa Indonesia.",
    )

    canonical_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Nama baku atau nama internasional penyakit.",
    )

    code = models.SlugField(
        max_length=100,
        unique=True,
    )

    category = models.CharField(
        max_length=100,
        blank=True,
    )

    description = models.TextField(
        blank=True,
    )

    is_priority = models.BooleanField(
        default=False,
        db_index=True,
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
        ordering = ["name"]
        indexes = [
            models.Index(
                fields=["is_priority", "is_active"],
                name="disease_priority_active_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class DiseaseAlias(models.Model):
    disease = models.ForeignKey(
        Disease,
        on_delete=models.CASCADE,
        related_name="aliases",
    )

    alias = models.CharField(
        max_length=200,
    )

    language = models.CharField(
        max_length=20,
        default="id",
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["alias"]
        constraints = [
            models.UniqueConstraint(
                fields=["disease", "alias"],
                name="unique_disease_alias",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.alias} → {self.disease.name}"


class Location(models.Model):
    class AdministrativeLevel(models.TextChoices):
        COUNTRY = "country", "Negara"
        PROVINCE = "province", "Provinsi"
        REGENCY = "regency", "Kabupaten"
        CITY = "city", "Kota"
        DISTRICT = "district", "Kecamatan"
        VILLAGE = "village", "Desa/Kelurahan"
        OTHER = "other", "Lainnya"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    name = models.CharField(
        max_length=200,
    )

    code = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
        help_text="Kode wilayah resmi jika tersedia.",
    )

    administrative_level = models.CharField(
        max_length=20,
        choices=AdministrativeLevel.choices,
        db_index=True,
    )

    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )

    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )

    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )

    country_code = models.CharField(
        max_length=2,
        default="ID",
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
            "administrative_level",
            "name",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "name",
                    "administrative_level",
                    "parent",
                    "country_code",
                ],
                name="unique_location_hierarchy",
            ),
        ]
        indexes = [
            models.Index(
                fields=["administrative_level", "name"],
                name="location_level_name_idx",
            ),
        ]

    def __str__(self) -> str:
        if self.parent:
            return f"{self.name}, {self.parent.name}"

        return self.name

class LocationAlias(models.Model):
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="aliases",
    )

    alias = models.CharField(
        max_length=200,
    )

    language = models.CharField(
        max_length=20,
        default="id",
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["alias"]
        constraints = [
            models.UniqueConstraint(
                fields=["location", "alias"],
                name="unique_location_alias",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.alias} → {self.location.name}"

class ArticleDisease(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    article = models.ForeignKey(
        Article,
        on_delete=models.CASCADE,
        related_name="article_diseases",
    )

    disease = models.ForeignKey(
        Disease,
        on_delete=models.PROTECT,
        related_name="article_mentions",
    )

    mention_text = models.CharField(
        max_length=300,
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

    extraction_method = models.CharField(
        max_length=20,
        choices=ExtractionMethod.choices,
        default=ExtractionMethod.SYSTEM,
    )

    is_primary = models.BooleanField(
        default=False,
    )

    validation_status = models.CharField(
        max_length=20,
        choices=ValidationStatus.choices,
        default=ValidationStatus.UNREVIEWED,
        db_index=True,
    )

    validation_notes = models.TextField(
        blank=True,
    )

    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="validated_article_diseases",
        null=True,
        blank=True,
    )

    validated_at = models.DateTimeField(
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
        constraints = [
            models.UniqueConstraint(
                fields=["article", "disease"],
                name="unique_article_disease",
            ),
        ]
        indexes = [
            models.Index(
                fields=["disease", "validation_status"],
                name="artdis_disease_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.article.title} — {self.disease.name}"

class ArticleLocation(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    article = models.ForeignKey(
        Article,
        on_delete=models.CASCADE,
        related_name="article_locations",
    )

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="article_mentions",
    )

    mention_text = models.CharField(
        max_length=300,
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

    extraction_method = models.CharField(
        max_length=20,
        choices=ExtractionMethod.choices,
        default=ExtractionMethod.SYSTEM,
    )

    is_primary = models.BooleanField(
        default=False,
    )

    validation_status = models.CharField(
        max_length=20,
        choices=ValidationStatus.choices,
        default=ValidationStatus.UNREVIEWED,
        db_index=True,
    )

    validation_notes = models.TextField(
        blank=True,
    )

    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="validated_article_locations",
        null=True,
        blank=True,
    )

    validated_at = models.DateTimeField(
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
        constraints = [
            models.UniqueConstraint(
                fields=["article", "location"],
                name="unique_article_location",
            ),
        ]
        indexes = [
            models.Index(
                fields=["location", "validation_status"],
                name="artloc_location_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.article.title} — {self.location.name}"

class ArticleFact(models.Model):
    class Trend(models.TextChoices):
        INCREASING = "increasing", "Meningkat"
        DECREASING = "decreasing", "Menurun"
        STABLE = "stable", "Stabil"
        NEW_OCCURRENCE = "new_occurrence", "Kejadian Baru"
        SPREADING = "spreading", "Meluas"
        UNKNOWN = "unknown", "Belum Diketahui"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    article = models.ForeignKey(
        Article,
        on_delete=models.CASCADE,
        related_name="facts",
    )

    disease = models.ForeignKey(
        Disease,
        on_delete=models.PROTECT,
        related_name="article_facts",
        null=True,
        blank=True,
    )

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="article_facts",
        null=True,
        blank=True,
    )

    event_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
    )

    case_count = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    death_count = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    recovery_count = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    hospitalized_count = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    trend = models.CharField(
        max_length=30,
        choices=Trend.choices,
        default=Trend.UNKNOWN,
        db_index=True,
    )

    affected_group = models.CharField(
        max_length=255,
        blank=True,
    )

    government_response = models.TextField(
        blank=True,
    )

    fact_text = models.TextField(
        blank=True,
        help_text="Potongan teks artikel yang mendukung fakta.",
    )

    confidence_score = models.FloatField(
        null=True,
        blank=True,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(1.0),
        ],
    )

    extraction_method = models.CharField(
        max_length=20,
        choices=ExtractionMethod.choices,
        default=ExtractionMethod.SYSTEM,
    )

    validation_status = models.CharField(
        max_length=20,
        choices=ValidationStatus.choices,
        default=ValidationStatus.UNREVIEWED,
        db_index=True,
    )

    validation_notes = models.TextField(
        blank=True,
    )

    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="validated_article_facts",
        null=True,
        blank=True,
    )

    validated_at = models.DateTimeField(
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
        ordering = ["-event_date", "-created_at"]
        indexes = [
            models.Index(
                fields=["disease", "location", "event_date"],
                name="artfact_dis_loc_date_idx",
            ),
            models.Index(
                fields=["validation_status", "trend"],
                name="artfact_status_trend_idx",
            ),
        ]

    def __str__(self) -> str:
        disease = self.disease.name if self.disease else "Tanpa penyakit"
        location = self.location.name if self.location else "Tanpa lokasi"

        return f"{disease} — {location}"

class ExtractionReviewLog(models.Model):
    class ObjectType(models.TextChoices):
        ARTICLE_DISEASE = (
            "article_disease",
            "Penyakit Artikel",
        )
        ARTICLE_LOCATION = (
            "article_location",
            "Lokasi Artikel",
        )
        ARTICLE_FACT = (
            "article_fact",
            "Fakta Artikel",
        )

    class Action(models.TextChoices):
        VALIDATE = "validate", "Validasi"
        CORRECT = "correct", "Koreksi"
        REJECT = "reject", "Penolakan"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    object_type = models.CharField(
        max_length=30,
        choices=ObjectType.choices,
        db_index=True,
    )

    object_id = models.UUIDField(
        db_index=True,
    )

    action = models.CharField(
        max_length=20,
        choices=Action.choices,
        db_index=True,
    )

    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="entity_extraction_reviews",
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
                fields=[
                    "object_type",
                    "object_id",
                    "reviewed_at",
                ],
                name="extract_review_object_idx",
            ),
            models.Index(
                fields=["reviewer", "reviewed_at"],
                name="extract_review_user_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.get_object_type_display()} - "
            f"{self.get_action_display()}"
        )