import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


def backfill_report_identity(apps, schema_editor):
    Report = apps.get_model("assessments", "IntelligenceReport")
    History = apps.get_model("assessments", "IntelligenceReportHistory")
    counters = {}

    reports = Report.objects.order_by("report_date", "created_at", "pk")
    for report in reports.iterator():
        year = report.report_date.year
        counters[year] = counters.get(year, 0) + 1
        report.code = f"LI-{year}-{counters[year]:04d}"
        report.updated_by_id = report.created_by_id
        report.save(update_fields=["code", "updated_by"])
        History.objects.create(
            report=report,
            action="created",
            from_status="",
            to_status=report.status,
            notes="Riwayat awal dibentuk saat aktivasi workspace laporan.",
            changed_by_id=report.created_by_id,
            metadata={"legacy_backfill": True},
        )


def reverse_backfill_report_identity(apps, schema_editor):
    Report = apps.get_model("assessments", "IntelligenceReport")
    History = apps.get_model("assessments", "IntelligenceReportHistory")
    History.objects.filter(metadata__legacy_backfill=True).delete()
    Report.objects.update(code=None, updated_by=None)


class Migration(migrations.Migration):

    dependencies = [
        ('articles', '0003_alter_article_locations_app'),
        ('assessments', '0006_intelligencereport_and_more'),
        ('entities', '0009_remove_location_state'),
        ('signals', '0003_alter_location_app'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='IntelligenceReportHistory',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('action', models.CharField(choices=[('created', 'Draf Dibentuk'), ('updated', 'Draf Diperbarui'), ('finalized', 'Difinalkan'), ('distributed', 'Didistribusikan'), ('archived', 'Diarsipkan')], db_index=True, max_length=20)),
                ('from_status', models.CharField(blank=True, choices=[('draft', 'Draft'), ('final', 'Final'), ('distributed', 'Didistribusikan'), ('archived', 'Arsip')], max_length=20)),
                ('to_status', models.CharField(choices=[('draft', 'Draft'), ('final', 'Final'), ('distributed', 'Didistribusikan'), ('archived', 'Arsip')], max_length=20)),
                ('notes', models.TextField(blank=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('changed_at', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                'ordering': ['-changed_at'],
            },
        ),
        migrations.AddField(
            model_name='intelligencereport',
            name='archived_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='intelligencereport',
            name='archived_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='archived_intelligence_reports', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='intelligencereport',
            name='code',
            field=models.CharField(blank=True, db_index=True, help_text='Kode produk intelijen, misalnya LI-2026-0001.', max_length=50, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='intelligencereport',
            name='distributed_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='intelligencereport',
            name='distributed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='distributed_intelligence_reports', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='intelligencereport',
            name='distribution_notes',
            field=models.TextField(blank=True, help_text='Catatan distribusi produk intelijen.'),
        ),
        migrations.AddField(
            model_name='intelligencereport',
            name='finalized_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='intelligencereport',
            name='finalized_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='finalized_intelligence_reports', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='intelligencereport',
            name='review_notes',
            field=models.TextField(blank=True, help_text='Catatan review terakhir sebelum laporan difinalkan.'),
        ),
        migrations.AddField(
            model_name='intelligencereport',
            name='updated_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='updated_intelligence_reports', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='intelligencereportsection',
            name='assessment_version',
            field=models.PositiveIntegerField(blank=True, help_text='Versi assessment yang menjadi dasar draft.', null=True),
        ),
        migrations.AddField(
            model_name='intelligencereportsection',
            name='recommendation_version',
            field=models.PositiveIntegerField(blank=True, help_text='Versi rekomendasi yang menjadi dasar draft.', null=True),
        ),
        migrations.AddField(
            model_name='intelligencereportsection',
            name='warning_version',
            field=models.PositiveIntegerField(blank=True, help_text='Versi peringatan dini yang menjadi dasar draft.', null=True),
        ),
        migrations.AddConstraint(
            model_name='intelligencereportsection',
            constraint=models.UniqueConstraint(fields=('report', 'order'), name='unique_report_section_order'),
        ),
        migrations.AddField(
            model_name='intelligencereporthistory',
            name='changed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='intelligence_report_history', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='intelligencereporthistory',
            name='report',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='history', to='assessments.intelligencereport'),
        ),
        migrations.RunPython(
            backfill_report_identity,
            reverse_backfill_report_identity,
        ),
    ]
