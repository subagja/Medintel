import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("assessments", "0005_intelligencerecommendation_and_more"),
        ("entities", "0009_remove_location_state"),
        ("signals", "0003_alter_location_app"),
        ("articles", "0003_alter_article_locations_app"),
    ]

    operations = [
        migrations.CreateModel(
            name="IntelligenceReport",
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
                    "kepada",
                    models.CharField(default="Yth. Pimpinan", max_length=200),
                ),
                ("dari", models.CharField(blank=True, max_length=200)),
                ("tembusan", models.CharField(blank=True, max_length=200)),
                (
                    "hal",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "Kosongkan untuk dibuat otomatis dari "
                            "tanggal laporan."
                        ),
                        max_length=300,
                    ),
                ),
                (
                    "nilai",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "Kode klasifikasi/nilai informasi, mis. C3."
                        ),
                        max_length=10,
                    ),
                ),
                ("report_date", models.DateField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("final", "Final"),
                            ("distributed", "Didistribusikan"),
                            ("archived", "Arsip"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=20,
                    ),
                ),
                (
                    "signature_block",
                    models.CharField(blank=True, max_length=200),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="intelligence_reports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-report_date", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="IntelligenceReportSection",
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
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("indikasi_text", models.TextField(blank=True)),
                ("analisis_text", models.TextField(blank=True)),
                ("dampak_text", models.TextField(blank=True)),
                ("upaya_text", models.TextField(blank=True)),
                ("saran_tindak_text", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "disease",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="report_sections",
                        to="entities.disease",
                    ),
                ),
                (
                    "signal",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="report_sections",
                        to="signals.signal",
                    ),
                ),
                (
                    "report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sections",
                        to="assessments.intelligencereport",
                    ),
                ),
                (
                    "source_articles",
                    models.ManyToManyField(
                        blank=True,
                        related_name="report_sections",
                        to="articles.article",
                    ),
                ),
            ],
            options={
                "ordering": ["report", "order"],
            },
        ),
    ]
