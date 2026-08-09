import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sources", "0004_draft_national_media_seed_patterns"),
    ]

    operations = [
        migrations.CreateModel(
            name="SourceDiscoveryQuery",
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
                    "provider",
                    models.CharField(
                        choices=[("google_news", "Google News RSS")],
                        db_index=True,
                        default="google_news",
                        max_length=30,
                    ),
                ),
                (
                    "query",
                    models.CharField(
                        help_text=(
                            "Istilah penyakit/kejadian yang dicari. Pembatas "
                            "site:domain ditambahkan otomatis oleh sistem."
                        ),
                        max_length=500,
                    ),
                ),
                (
                    "language",
                    models.CharField(
                        default="id",
                        help_text="Kode bahasa, misalnya id atau en.",
                        max_length=8,
                    ),
                ),
                (
                    "country",
                    models.CharField(
                        default="ID",
                        help_text="Kode negara ISO dua huruf, misalnya ID.",
                        max_length=2,
                    ),
                ),
                (
                    "max_age_days",
                    models.PositiveSmallIntegerField(
                        default=7,
                        help_text=(
                            "Entri yang lebih lama dari nilai ini tidak "
                            "diproses. Rentang yang disarankan 1–30 hari."
                        ),
                    ),
                ),
                (
                    "priority",
                    models.PositiveSmallIntegerField(
                        default=100,
                        help_text="Angka lebih kecil diproses lebih dahulu.",
                    ),
                ),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="discovery_queries",
                        to="sources.source",
                    ),
                ),
            ],
            options={
                "ordering": ["priority", "provider", "query"],
                "indexes": [
                    models.Index(
                        fields=["source", "provider", "is_active"],
                        name="source_discovery_active_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "source",
                            "provider",
                            "query",
                            "language",
                            "country",
                        ),
                        name="unique_source_discovery_query",
                    )
                ],
            },
        ),
    ]
