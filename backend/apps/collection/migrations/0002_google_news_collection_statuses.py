from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("collection", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="collectionjob",
            name="job_type",
            field=models.CharField(
                choices=[
                    ("crawler", "Crawler"),
                    ("rss", "RSS Feed"),
                    ("google_news", "Google News RSS"),
                    ("manual_url", "Input URL Manual"),
                    ("manual_article", "Input Artikel Manual"),
                ],
                db_index=True,
                default="crawler",
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="collectionjobitem",
            name="status",
            field=models.CharField(
                choices=[
                    ("found", "Ditemukan"),
                    ("created", "Dibuat"),
                    ("duplicate", "Duplikat"),
                    ("rejected", "Ditolak"),
                    ("failed", "Gagal"),
                    ("metadata_only", "Metadata Saja"),
                    ("fetch_blocked", "Pengambilan Terblokir"),
                ],
                db_index=True,
                default="found",
                max_length=20,
            ),
        ),
    ]
