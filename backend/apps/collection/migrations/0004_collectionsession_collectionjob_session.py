import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("collection", "0003_collectionjob_source_nullable"),
        ("sources", "0005_sourcediscoveryquery"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CollectionSession",
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
                    "scope",
                    models.CharField(
                        choices=[
                            ("all", "Semua Source siap"),
                            (
                                "all_indonesia",
                                "Semua Source Indonesia siap",
                            ),
                            ("single_source", "Satu Source"),
                        ],
                        db_index=True,
                        default="all",
                        max_length=30,
                    ),
                ),
                ("include_google_news", models.BooleanField(default=True)),
                ("html_deep_scan", models.BooleanField(default=False)),
                (
                    "article_limit",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                (
                    "candidate_limit",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("planned_job_count", models.PositiveIntegerField(default=0)),
                ("skipped_job_count", models.PositiveIntegerField(default=0)),
                (
                    "trigger_type",
                    models.CharField(default="system", max_length=30),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "selected_source",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="selected_collection_sessions",
                        to="sources.source",
                    ),
                ),
                (
                    "triggered_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="triggered_collection_sessions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="collectionsession",
            index=models.Index(
                fields=["scope", "created_at"],
                name="collsess_scope_created_idx",
            ),
        ),
        migrations.AddField(
            model_name="collectionjob",
            name="session",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Sesi Koleksi Terpadu yang menaungi job ini, bila ada."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="jobs",
                to="collection.collectionsession",
            ),
        ),
    ]
