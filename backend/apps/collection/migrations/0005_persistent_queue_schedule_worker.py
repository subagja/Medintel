# Generated for MedIntel persistent collection queue.

import datetime
import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


def populate_queue_keys(apps, schema_editor):
    CollectionJob = apps.get_model("collection", "CollectionJob")
    running_keys = set()
    for job in CollectionJob.objects.order_by("created_at").iterator():
        scope = f"source:{job.source_id}" if job.source_id else "global"
        job.queue_key = f"{scope}:{job.job_type}"
        if job.status == "running" and not job.heartbeat_at:
            job.heartbeat_at = job.updated_at or job.started_at or job.created_at
        update_fields = ["queue_key", "heartbeat_at"]
        if job.status == "running" and job.queue_key in running_keys:
            job.status = "retry_waiting"
            job.available_at = django.utils.timezone.now()
            job.error_message = (
                "Dipindahkan ke antrean saat migrasi karena sumber-kanal "
                "yang sama tercatat berjalan lebih dari satu kali."
            )
            update_fields.extend(["status", "available_at", "error_message"])
        elif job.status == "running":
            running_keys.add(job.queue_key)
        job.save(update_fields=update_fields)


class Migration(migrations.Migration):
    dependencies = [
        ("collection", "0004_collectionsession_collectionjob_session"),
        ("requirements", "0003_requirementhistory_requirementinformationgap_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="collectionjob",
            name="attempt_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="collectionjob",
            name="available_at",
            field=models.DateTimeField(
                db_index=True,
                default=django.utils.timezone.now,
                help_text="Waktu paling awal job boleh diklaim worker.",
            ),
        ),
        migrations.AddField(
            model_name="collectionjob",
            name="cancel_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="collectionjob",
            name="heartbeat_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="collectionjob",
            name="last_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="collectionjob",
            name="lease_expires_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="collectionjob",
            name="max_attempts",
            field=models.PositiveSmallIntegerField(
                default=3,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(10),
                ],
            ),
        ),
        migrations.AddField(
            model_name="collectionjob",
            name="pause_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="collectionjob",
            name="queue_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Kunci eksklusif sumber-kanal selama job berjalan.",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="collectionjob",
            name="rerun_of",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reruns",
                to="collection.collectionjob",
            ),
        ),
        migrations.AddField(
            model_name="collectionjob",
            name="worker_id",
            field=models.CharField(blank=True, db_index=True, max_length=150),
        ),
        migrations.AlterField(
            model_name="collectionjob",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Menunggu"),
                    ("running", "Sedang Berjalan"),
                    ("completed", "Selesai"),
                    ("completed_with_errors", "Selesai dengan Kesalahan"),
                    ("failed", "Gagal"),
                    ("cancelled", "Dibatalkan"),
                    ("paused", "Dijeda"),
                    ("retry_waiting", "Menunggu Percobaan Ulang"),
                ],
                db_index=True,
                default="pending",
                max_length=30,
            ),
        ),
        migrations.RunPython(populate_queue_keys, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="collectionjob",
            index=models.Index(
                fields=["status", "available_at", "created_at"],
                name="colljob_queue_ready_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="collectionjob",
            constraint=models.UniqueConstraint(
                condition=Q(status="running") & ~Q(queue_key=""),
                fields=("queue_key",),
                name="uniq_running_coll_queue_key",
            ),
        ),
        migrations.CreateModel(
            name="CollectionWorker",
            fields=[
                ("id", models.CharField(max_length=150, primary_key=True, serialize=False)),
                ("hostname", models.CharField(max_length=150)),
                ("process_id", models.PositiveIntegerField()),
                ("status", models.CharField(choices=[("active", "Aktif"), ("stopped", "Berhenti")], db_index=True, default="active", max_length=20)),
                ("concurrency", models.PositiveSmallIntegerField(default=1)),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_heartbeat_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("stopped_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
            options={"ordering": ["-last_heartbeat_at"]},
        ),
        migrations.CreateModel(
            name="CollectionJobLog",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("level", models.CharField(choices=[("info", "Informasi"), ("warning", "Peringatan"), ("error", "Kesalahan")], db_index=True, default="info", max_length=20)),
                ("event", models.CharField(db_index=True, max_length=50)),
                ("message", models.TextField()),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="logs", to="collection.collectionjob")),
            ],
            options={
                "ordering": ["created_at", "id"],
                "indexes": [models.Index(fields=["job", "created_at"], name="colljoblog_job_created_idx")],
            },
        ),
        migrations.CreateModel(
            name="CollectionSchedule",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=200)),
                ("recurrence", models.CharField(choices=[("hourly", "Setiap Jam"), ("every_6_hours", "Setiap 6 Jam"), ("daily", "Harian"), ("weekly", "Mingguan")], db_index=True, default="daily", max_length=30)),
                ("run_time", models.TimeField(default=datetime.time(6, 0), help_text="Dipakai untuk jadwal harian dan mingguan.")),
                ("weekday", models.PositiveSmallIntegerField(default=0, help_text="0=Senin sampai 6=Minggu.", validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(6)])),
                ("include_google_news", models.BooleanField(default=True)),
                ("html_deep_scan", models.BooleanField(default=False)),
                ("article_limit", models.PositiveIntegerField(blank=True, null=True)),
                ("candidate_limit", models.PositiveIntegerField(blank=True, null=True)),
                ("max_attempts", models.PositiveSmallIntegerField(default=3, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(10)])),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("next_run_at", models.DateTimeField(db_index=True)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_collection_schedules", to=settings.AUTH_USER_MODEL)),
                ("intelligence_requirement", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="collection_schedules", to="requirements.intelligencerequirement")),
                ("last_session", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="schedule_runs", to="collection.collectionsession")),
                ("source", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="collection_schedules", to="sources.source")),
            ],
            options={
                "ordering": ["-is_active", "next_run_at", "name"],
                "indexes": [models.Index(fields=["is_active", "next_run_at"], name="collsched_due_idx")],
            },
        ),
    ]
