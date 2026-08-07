# Migrasi manual (2/4 dari pemindahan Location -> app locations).
# State-only: FK ArticleLocation.location & ArticleFact.location sekarang
# menunjuk ke locations.Location. Tidak ada perubahan kolom/constraint di
# database karena Location tetap di tabel yang sama (entities_location).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entities", "0007_seed_full_surveillance_diseases"),
        ("locations", "0001_move_location_from_entities"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="articlelocation",
                    name="location",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="article_mentions",
                        to="locations.location",
                    ),
                ),
                migrations.AlterField(
                    model_name="articlefact",
                    name="location",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="article_facts",
                        to="locations.location",
                    ),
                ),
            ],
        ),
    ]
