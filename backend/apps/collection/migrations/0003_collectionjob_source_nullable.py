from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("collection", "0002_google_news_collection_statuses"),
        ("sources", "0005_sourcediscoveryquery"),
    ]

    operations = [
        migrations.AlterField(
            model_name="collectionjob",
            name="source",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Kosong untuk job discovery lintas sumber. Source artikel "
                    "ditentukan setelah URL penerbit berhasil diselesaikan."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="collection_jobs",
                to="sources.source",
            ),
        ),
    ]
