# Generated for Medintel controlled advanced update.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intel", "0012_signalcluster_signal_approval_recommendation_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="DiseaseMaster",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(db_index=True, max_length=200, unique=True)),
                ("normalized_name", models.CharField(blank=True, db_index=True, default="", max_length=200)),
                ("aliases", models.TextField(blank=True, default="")),
                ("skdr_code", models.CharField(blank=True, db_index=True, default="", max_length=20)),
                ("skdr_priority", models.BooleanField(db_index=True, default=False)),
                ("report_24h", models.BooleanField(db_index=True, default=False)),
                ("emerging_watchlist", models.BooleanField(db_index=True, default=False)),
                ("reemerging_watch", models.BooleanField(db_index=True, default=False)),
                ("disease_type", models.CharField(choices=[("vector_borne", "Vector-borne"), ("zoonosis", "Zoonosis"), ("respiratory", "Respiratory"), ("foodborne", "Foodborne"), ("waterborne", "Waterborne"), ("vaccine_preventable", "Vaccine-preventable"), ("unknown_cluster", "Unknown Cluster"), ("other", "Other")], db_index=True, default="other", max_length=40)),
                ("severity_weight", models.CharField(choices=[("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")], db_index=True, default="medium", max_length=20)),
                ("alert_rule", models.CharField(choices=[("trend_based", "Trend-based"), ("immediate", "Immediate"), ("novelty_based", "Novelty-based"), ("reemerging", "Re-emerging"), ("unknown_cluster", "Unknown Cluster")], db_index=True, default="trend_based", max_length=40)),
                ("keyword_id", models.TextField(blank=True, default="")),
                ("keyword_en", models.TextField(blank=True, default="")),
                ("notes", models.TextField(blank=True, default="")),
                ("is_active", models.BooleanField(db_index=True, default=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="signal",
            name="disease_master",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="signals", to="intel.diseasemaster"),
        ),
        migrations.AddField(
            model_name="signalcluster",
            name="disease_master",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="clusters", to="intel.diseasemaster"),
        ),
        migrations.AddIndex(
            model_name="diseasemaster",
            index=models.Index(fields=["normalized_name"], name="intel_dise_normal_9e87b2_idx"),
        ),
        migrations.AddIndex(
            model_name="diseasemaster",
            index=models.Index(fields=["skdr_priority"], name="intel_dise_skdr_pr_963b5c_idx"),
        ),
        migrations.AddIndex(
            model_name="diseasemaster",
            index=models.Index(fields=["report_24h"], name="intel_dise_report__83c8e9_idx"),
        ),
        migrations.AddIndex(
            model_name="diseasemaster",
            index=models.Index(fields=["emerging_watchlist"], name="intel_dise_emergin_62bc14_idx"),
        ),
        migrations.AddIndex(
            model_name="diseasemaster",
            index=models.Index(fields=["reemerging_watch"], name="intel_dise_reemerg_85f9c4_idx"),
        ),
        migrations.AddIndex(
            model_name="diseasemaster",
            index=models.Index(fields=["alert_rule"], name="intel_dise_alert_r_fcb6f7_idx"),
        ),
        migrations.AddIndex(
            model_name="diseasemaster",
            index=models.Index(fields=["is_active"], name="intel_dise_is_acti_3bbddb_idx"),
        ),
    ]
