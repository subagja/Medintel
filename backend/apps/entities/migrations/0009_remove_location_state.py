# Migrasi manual (4/4, TERAKHIR dari pemindahan Location -> app locations).
#
# State-only: melepas Location & LocationAlias dari state migrasi app
# `entities`. Wajib berjalan PALING TERAKHIR, setelah semua app lain
# (articles, indicators, requirements, signals, dan entities sendiri di
# migrasi 0008) berhenti menunjuk ke entities.Location. Tidak ada operasi
# database sama sekali di sini -- tabel entities_location dan
# entities_locationalias tetap ada, hanya sekarang "dimiliki" sepenuhnya
# oleh app locations (lihat locations/migrations/0001).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("entities", "0008_alter_articlelocation_articlefact_location_app"),
        ("articles", "0003_alter_article_locations_app"),
        ("indicators", "0003_alter_indicator_location_app"),
        ("requirements", "0002_alter_location_app"),
        ("signals", "0003_alter_location_app"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="locationalias",
                    name="location",
                ),
                migrations.DeleteModel(
                    name="LocationAlias",
                ),
                migrations.RemoveField(
                    model_name="location",
                    name="parent",
                ),
                migrations.DeleteModel(
                    name="Location",
                ),
            ],
        ),
    ]
