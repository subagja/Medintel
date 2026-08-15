import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("collection", "0005_persistent_queue_schedule_worker"),
    ]

    operations = [
        migrations.AlterField(
            model_name="collectionschedule",
            name="source",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="collection_schedules",
                to="sources.source",
            ),
        ),
        migrations.AddField(
            model_name="collectionschedule",
            name="source_scope",
            field=models.CharField(
                choices=[
                    ("all", "Semua Source siap"),
                    ("all_indonesia", "Semua Source Indonesia siap"),
                    ("single_source", "Satu Source"),
                ],
                default="single_source",
                help_text=(
                    "Gunakan semua sumber siap, semua sumber Indonesia, "
                    "atau satu sumber tertentu."
                ),
                max_length=30,
            ),
        ),
        migrations.AddConstraint(
            model_name="collectionschedule",
            constraint=models.CheckConstraint(
                condition=(
                    Q(source_scope="single_source", source__isnull=False)
                    | Q(
                        source_scope__in=("all", "all_indonesia"),
                        source__isnull=True,
                    )
                ),
                name="collsched_scope_source_valid",
            ),
        ),
    ]
