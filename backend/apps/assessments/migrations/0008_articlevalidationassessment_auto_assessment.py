from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0007_intelligencereporthistory_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="articlevalidationassessment",
            name="auto_assessment_method",
            field=models.CharField(
                blank=True,
                choices=[
                    ("rule_based", "Rule-based"),
                    ("ai_assisted", "AI-assisted"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="articlevalidationassessment",
            name="auto_assessment_version",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="articlevalidationassessment",
            name="auto_assessed_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="articlevalidationassessment",
            name="auto_recommendation",
            field=models.CharField(
                blank=True,
                choices=[
                    ("recommend_validate", "Direkomendasikan valid"),
                    ("needs_review", "Perlu pemeriksaan analis"),
                    (
                        "recommend_reject",
                        "Direkomendasikan tidak relevan",
                    ),
                ],
                db_index=True,
                max_length=30,
            ),
        ),
    ]
