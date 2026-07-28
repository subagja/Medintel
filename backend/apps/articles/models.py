import uuid

from django.db import models

from apps.sources.models import Source


class Article(models.Model):
    class ProcessingStatus(models.TextChoices):
        NEW = "new", "Baru"
        VALIDATED = "validated", "Tervalidasi"
        PROCESSING = "processing", "Sedang Diproses"
        PROCESSED = "processed", "Selesai Diproses"
        REJECTED = "rejected", "Ditolak"
        FAILED = "failed", "Gagal"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    source = models.ForeignKey(
        Source,
        on_delete=models.PROTECT,
        related_name="articles",
    )

    original_url = models.URLField(
        max_length=1000,
    )

    normalized_url = models.URLField(
        max_length=1000,
        unique=True,
    )

    title = models.CharField(
        max_length=500,
    )

    content_text = models.TextField()

    excerpt = models.TextField(
        blank=True,
    )

    author = models.CharField(
        max_length=255,
        blank=True,
    )

    published_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    crawled_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    content_hash = models.CharField(
        max_length=64,
        db_index=True,
    )

    processing_status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.NEW,
        db_index=True,
    )

    rejection_reason = models.TextField(
        blank=True,
    )

    raw_metadata = models.JSONField(
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
        ordering = ["-published_at", "-crawled_at"]
        indexes = [
            models.Index(
                fields=["source", "published_at"],
                name="article_source_pub_idx",
            ),
            models.Index(
                fields=["processing_status", "crawled_at"],
                name="article_status_crawl_idx",
            ),
            models.Index(
                fields=["content_hash", "source"],
                name="article_hash_source_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "content_hash"],
                name="unique_article_source_content",
            ),
        ]

    def __str__(self) -> str:
        return self.title