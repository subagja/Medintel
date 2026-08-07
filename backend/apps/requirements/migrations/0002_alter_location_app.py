# Migrasi manual (3/4). State-only: FK RequirementLocation.location dan M2M
# IntelligenceRequirement.locations dialihkan ke locations.Location.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("requirements", "0001_initial"),
        ("locations", "0001_move_location_from_entities"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="requirementlocation",
                    name="location",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="requirement_links",
                        to="locations.location",
                    ),
                ),
                migrations.AlterField(
                    model_name="intelligencerequirement",
                    name="locations",
                    field=models.ManyToManyField(
                        blank=True,
                        related_name="intelligence_requirements",
                        through="requirements.RequirementLocation",
                        to="locations.location",
                    ),
                ),
            ],
        ),
    ]
