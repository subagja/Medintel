import uuid

from django.conf import settings
from django.db import models

from apps.articles.models import Article
from apps.sources.models import Source


class CollectionJob(models.Model):
    class JobType(models.TextChoices):
        CRAWLER = "crawler", "Crawler"
        RSS = "rss", "RSS Feed"
        MANUAL_URL = "manual_url", "Input URL Manual"
        MANUAL_ARTICLE = "manual_article", "Input Artikel Manual"

    class Status(models.TextChoices):
        PENDING = "pending", "Menunggu"
        RUNNING = "running", "Sedang Berjalan"
        COMPLETED = "completed", "Selesai"
        COMPLETED_WITH_ERRORS = (
            "completed_with_errors",
            "Selesai dengan Kesalahan",
        )
        FAILED = "failed", "Gagal"
        CANCELLED = "cancelled", "Dibatalkan"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    source = models.ForeignKey(
        Source,
        on_delete=models.PROTECT,
        related_name="collection_jobs",
    )

    job_type = models.CharField(
        max_length=30,
        choices=JobType.choices,
        default=JobType.CRAWLER,
        db_index=True,
    )

    crawler_name = models.CharField(
        max_length=150,
        blank=True,
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    started_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    finished_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    total_found = models.PositiveIntegerField(
        default=0,
    )

    total_created = models.PositiveIntegerField(
        default=0,
    )

    total_duplicate = models.PositiveIntegerField(
        default=0,
    )

    total_rejected = models.PositiveIntegerField(
        default=0,
    )

    total_failed = models.PositiveIntegerField(
        default=0,
    )

    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="triggered_collection_jobs",
        null=True,
        blank=True,
    )

    trigger_type = models.CharField(
        max_length=30,
        default="system",
        help_text="Contoh: system, schedule, user, atau management_command.",
    )

    error_message = models.TextField(
        blank=True,
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["source", "status", "created_at"],
                name="colljob_src_status_idx",
            ),
            models.Index(
                fields=["job_type", "created_at"],
                name="colljob_type_created_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.source.code} - "
            f"{self.get_job_type_display()} - "
            f"{self.get_status_display()}"
        )


class CollectionJobItem(models.Model):
    class Status(models.TextChoices):
        FOUND = "found", "Ditemukan"
        CREATED = "created", "Dibuat"
        DUPLICATE = "duplicate", "Duplikat"
        REJECTED = "rejected", "Ditolak"
        FAILED = "failed", "Gagal"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    collection_job = models.ForeignKey(
        CollectionJob,
        on_delete=models.CASCADE,
        related_name="items",
    )

    article = models.ForeignKey(
        Article,
        on_delete=models.SET_NULL,
        related_name="collection_items",
        null=True,
        blank=True,
    )

    original_url = models.URLField(
        max_length=1000,
    )

    normalized_url = models.URLField(
        max_length=1000,
        blank=True,
    )

    title = models.CharField(
        max_length=500,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.FOUND,
        db_index=True,
    )

    reason = models.TextField(
        blank=True,
    )

    error_message = models.TextField(
        blank=True,
    )

    processed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["collection_job", "status"],
                name="collitem_job_status_idx",
            ),
            models.Index(
                fields=["normalized_url"],
                name="collitem_norm_url_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["collection_job", "original_url"],
                name="unique_collection_job_url",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_status_display()} - {self.original_url}"