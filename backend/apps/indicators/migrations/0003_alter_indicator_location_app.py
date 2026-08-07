# Migrasi manual (3/4). State-only: FK Indicator.location dialihkan ke
# locations.Location.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("indicators", "0002_indicatorreviewlog"),
        ("locations", "0001_move_location_from_entities"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="indicator",
                    name="location",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="indicators",
                        to="locations.location",
                    ),
                ),
            ],
        ),
    ]
