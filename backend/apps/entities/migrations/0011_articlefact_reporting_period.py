from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("entities", "0010_diseasecandidate"),
    ]

    operations = [
        migrations.AddField(
            model_name="articlefact",
            name="reporting_period_start",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="articlefact",
            name="reporting_period_end",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="articlefact",
            name="reporting_period_precision",
            field=models.CharField(
                choices=[
                    ("unspecified", "Belum ditentukan"),
                    ("day_range", "Rentang tanggal"),
                    ("month", "Rentang bulan"),
                    ("week", "Rentang minggu"),
                    ("year", "Rentang tahun"),
                ],
                default="unspecified",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="articlefact",
            name="case_count_basis",
            field=models.CharField(
                choices=[
                    ("unknown", "Belum diketahui"),
                    ("cumulative", "Kumulatif selama periode"),
                    ("new", "Kasus baru selama periode"),
                ],
                default="unknown",
                max_length=20,
            ),
        ),
    ]
