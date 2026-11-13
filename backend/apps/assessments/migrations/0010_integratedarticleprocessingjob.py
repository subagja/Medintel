# MEDINTEL 02B8A: new job-tracking table only; no existing article data altered.
import uuid
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('assessments', '0009_bulkarticlevalidationjob'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='IntegratedArticleProcessingJob',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, serialize=False, editable=False)),
                ('active_key', models.CharField(max_length=40, default='integrated_processing', editable=False)),
                ('status', models.CharField(max_length=32, db_index=True, default='queued',
                    choices=[('queued','Menunggu worker'),('running','Sedang diproses'),
                             ('completed','Selesai'),('completed_with_errors','Selesai dengan error'),
                             ('failed','Terhenti')])),
                ('mode', models.CharField(max_length=12, default='new')),
                ('batch_size', models.PositiveSmallIntegerField(default=5)),
                ('total_items', models.PositiveSmallIntegerField(default=0)),
                ('processed_items', models.PositiveSmallIntegerField(default=0)),
                ('candidate_ids', models.JSONField(default=list, blank=True)),
                ('finished_ids', models.JSONField(default=list, blank=True)),
                ('results', models.JSONField(default=list, blank=True)),
                ('stats', models.JSONField(default=dict, blank=True)),
                ('auto_approve', models.BooleanField(default=False)),
                ('ai_case_evidence', models.BooleanField(default=False)),
                ('current_article_id', models.CharField(max_length=36, blank=True)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('started_at', models.DateTimeField(null=True, blank=True)),
                ('completed_at', models.DateTimeField(null=True, blank=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('requested_by', models.ForeignKey(
                    to=settings.AUTH_USER_MODEL, null=True, blank=True,
                    related_name='integrated_processing_jobs', on_delete=django.db.models.deletion.SET_NULL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddConstraint(
            model_name='integratedarticleprocessingjob',
            constraint=models.UniqueConstraint(
                fields=('active_key',),
                condition=Q(status__in=['queued', 'running']),
                name='uniq_active_integrated_job'),
        ),
    ]
