import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        (
            "assessments",
            "0008_articlevalidationassessment_auto_assessment",
        ),
    ]

    operations = [
        migrations.CreateModel(
            name="BulkArticleValidationJob",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "singleton_key",
                    models.CharField(
                        default="article_bulk_validation",
                        editable=False,
                        max_length=40,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Menunggu"),
                            ("running", "Diproses"),
                            ("completed", "Selesai"),
                            ("failed", "Gagal"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=20,
                    ),
                ),
                (
                    "batch_size",
                    models.PositiveSmallIntegerField(default=50),
                ),
                (
                    "total_items",
                    models.PositiveIntegerField(default=0),
                ),
                (
                    "processed_items",
                    models.PositiveIntegerField(default=0),
                ),
                (
                    "error_count",
                    models.PositiveIntegerField(default=0),
                ),
                (
                    "summary",
                    models.JSONField(blank=True, default=dict),
                ),
                (
                    "error_message",
                    models.TextField(blank=True),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "started_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "completed_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="bulk_article_validation_jobs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["status", "created_at"],
                        name="bulk_article_job_status_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(
                            status__in=["queued", "running"],
                        ),
                        fields=("singleton_key",),
                        name="uniq_active_article_bulk_job",
                    ),
                ],
            },
        ),
    ]
