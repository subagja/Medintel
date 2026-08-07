# Migrasi manual (3/4). State-only: seluruh referensi Location di app signals
# (Signal.primary_location, SignalLocation.location, M2M Signal.locations)
# dialihkan ke locations.Location.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("signals", "0002_signal_analyst_judgement_signal_implication_and_more"),
        ("locations", "0001_move_location_from_entities"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="signal",
                    name="primary_location",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="primary_signals",
                        to="locations.location",
                    ),
                ),
                migrations.AlterField(
                    model_name="signallocation",
                    name="location",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="signal_links",
                        to="locations.location",
                    ),
                ),
                migrations.AlterField(
                    model_name="signal",
                    name="locations",
                    field=models.ManyToManyField(
                        blank=True,
                        related_name="signals",
                        through="signals.SignalLocation",
                        to="locations.location",
                    ),
                ),
            ],
        ),
    ]
