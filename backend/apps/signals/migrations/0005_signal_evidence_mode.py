from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("signals", "0004_signal_event_classification"),
    ]

    operations = [
        migrations.AddField(
            model_name="signal",
            name="evidence_mode",
            field=models.CharField(
                choices=[
                    ("quantitative", "Kuantitatif"),
                    ("qualitative", "Kualitatif"),
                ],
                db_index=True,
                default="quantitative",
                help_text=(
                    "Jalur bukti awal pembentukan sinyal. Sinyal "
                    "kualitatif tetap memerlukan konfirmasi analis."
                ),
                max_length=20,
            ),
        ),
    ]
