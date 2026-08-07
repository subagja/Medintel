# Migrasi manual (3/4). State-only: target ManyToManyField Article.locations
# dialihkan ke locations.Location. Tabel through (entities.ArticleLocation)
# tidak berubah.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("articles", "0002_article_diseases_article_locations"),
        ("locations", "0001_move_location_from_entities"),
        ("entities", "0008_alter_articlelocation_articlefact_location_app"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="article",
                    name="locations",
                    field=models.ManyToManyField(
                        blank=True,
                        related_name="articles",
                        through="entities.ArticleLocation",
                        to="locations.location",
                    ),
                ),
            ],
        ),
    ]
