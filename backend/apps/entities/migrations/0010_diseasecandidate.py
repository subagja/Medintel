import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("articles", "0003_alter_article_locations_app"),
        ("entities", "0009_remove_location_state"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DiseaseCandidate",
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
                ("proposed_name", models.CharField(max_length=200)),
                ("canonical_name", models.CharField(blank=True, max_length=200)),
                (
                    "agent_type",
                    models.CharField(
                        choices=[
                            ("virus", "Virus"),
                            ("bacterium", "Bakteri"),
                            ("fungus", "Jamur"),
                            ("parasite", "Parasit"),
                            ("prion", "Prion"),
                            ("other", "Lainnya"),
                            ("unknown", "Belum diketahui"),
                        ],
                        default="unknown",
                        max_length=20,
                    ),
                ),
                ("justification", models.TextField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Menunggu persetujuan"),
                            ("approved", "Disetujui"),
                            ("rejected", "Ditolak"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("review_notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "approved_disease",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_candidates",
                        to="entities.disease",
                    ),
                ),
                (
                    "article",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="disease_candidates",
                        to="articles.article",
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_disease_candidates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "submitted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="submitted_disease_candidates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="diseasecandidate",
            index=models.Index(
                fields=["article", "status"],
                name="discan_article_status_idx",
            ),
        ),
    ]
