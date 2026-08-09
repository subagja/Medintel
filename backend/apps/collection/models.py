import uuid

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from apps.articles.models import Article
from apps.sources.models import Source


class CollectionSession(models.Model):
    """Satu eksekusi operasional yang dapat memuat beberapa kanal crawler."""

    class Scope(models.TextChoices):
        ALL_READY = "all", "Semua Source siap"
        ALL_INDONESIA = "all_indonesia", "Semua Source Indonesia siap"
        SINGLE_SOURCE = "single_source", "Satu Source"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    scope = models.CharField(
        max_length=30,
        choices=Scope.choices,
        default=Scope.ALL_READY,
        db_index=True,
    )
    selected_source = models.ForeignKey(
        Source,
        on_delete=models.PROTECT,
        related_name="selected_collection_sessions",
        null=True,
        blank=True,
    )
    include_google_news = models.BooleanField(default=True)
    html_deep_scan = models.BooleanField(default=False)
    article_limit = models.PositiveIntegerField(null=True, blank=True)
    candidate_limit = models.PositiveIntegerField(null=True, blank=True)
    planned_job_count = models.PositiveIntegerField(default=0)
    skipped_job_count = models.PositiveIntegerField(default=0)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="triggered_collection_sessions",
        null=True,
        blank=True,
    )
    trigger_type = models.CharField(max_length=30, default="system")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["scope", "created_at"],
                name="collsess_scope_created_idx",
            ),
        ]

    @property
    def reference(self) -> str:
        created = self.created_at or timezone.now()
        local_created = timezone.localtime(created)
        return f"KOL-{local_created:%Y%m%d}-{str(self.id)[:8].upper()}"

    @property
    def status(self) -> str:
        statuses = list(self.jobs.values_list("status", flat=True))
        if not statuses:
            return CollectionJob.Status.PENDING
        if any(
            status in {CollectionJob.Status.PENDING, CollectionJob.Status.RUNNING}
            for status in statuses
        ):
            return CollectionJob.Status.RUNNING
        if all(status == CollectionJob.Status.CANCELLED for status in statuses):
            return CollectionJob.Status.CANCELLED
        if all(
            status in {CollectionJob.Status.FAILED, CollectionJob.Status.CANCELLED}
            for status in statuses
        ):
            return CollectionJob.Status.FAILED
        if any(
            status in {
                CollectionJob.Status.FAILED,
                CollectionJob.Status.COMPLETED_WITH_ERRORS,
                CollectionJob.Status.CANCELLED,
            }
            for status in statuses
        ):
            return CollectionJob.Status.COMPLETED_WITH_ERRORS
        return CollectionJob.Status.COMPLETED

    @property
    def status_label(self) -> str:
        return dict(CollectionJob.Status.choices).get(self.status, self.status)

    @property
    def totals(self) -> dict[str, int]:
        values = self.jobs.aggregate(
            total_found=Sum("total_found"),
            total_created=Sum("total_created"),
            total_duplicate=Sum("total_duplicate"),
            total_rejected=Sum("total_rejected"),
            total_failed=Sum("total_failed"),
        )
        return {key: value or 0 for key, value in values.items()}

    def __str__(self) -> str:
        return f"{self.reference} - {self.status_label}"


class CollectionJob(models.Model):
    class JobType(models.TextChoices):
        CRAWLER = "crawler", "Crawler"
        RSS = "rss", "RSS Feed"
        GOOGLE_NEWS = "google_news", "Google News RSS"
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

    session = models.ForeignKey(
        CollectionSession,
        on_delete=models.SET_NULL,
        related_name="jobs",
        null=True,
        blank=True,
        help_text="Sesi Koleksi Terpadu yang menaungi job ini, bila ada.",
    )

    source = models.ForeignKey(
        Source,
        on_delete=models.PROTECT,
        related_name="collection_jobs",
        null=True,
        blank=True,
        help_text=(
            "Kosong untuk job discovery lintas sumber. Source artikel "
            "ditentukan setelah URL penerbit berhasil diselesaikan."
        ),
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
        source_label = (
            self.source.code
            if self.source_id
            else "lintas-sumber"
        )
        return (
            f"{source_label} - "
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
        METADATA_ONLY = "metadata_only", "Metadata Saja"
        FETCH_BLOCKED = "fetch_blocked", "Pengambilan Terblokir"

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
