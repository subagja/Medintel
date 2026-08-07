# Migrasi manual: memindahkan model Location & LocationAlias dari app
# `entities` ke app `locations`.
#
# Menggunakan SeparateDatabaseAndState supaya operasi ini HANYA mengubah
# state migrasi Django (siapa "pemilik" model), TANPA menyentuh tabel fisik
# di database. Tabel `entities_location` dan `entities_locationalias` yang
# sudah dibuat oleh apps/entities/migrations/0001_initial.py dan
# 0002_locationalias.py tetap dipakai apa adanya (lihat db_table di
# apps/locations/models.py).
#
# Urutan penerapan migrasi (lihat juga apps/entities/migrations/0008_*.py):
#   1. locations.0001 (file ini)      -> daftarkan state Location di sini
#   2. entities.0008                  -> alihkan FK internal (ArticleLocation,
#                                         ArticleFact) ke locations.Location
#   3. articles/indicators/requirements/signals -> alihkan FK/M2M mereka
#   4. entities.0009                  -> hapus state Location dari entities

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        # Tabel fisik entities_location & entities_locationalias harus sudah
        # ada sebelum kita "mengklaim" state-nya di sini.
        ("entities", "0002_locationalias"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="Location",
                    fields=[
                        ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                        ("name", models.CharField(max_length=200)),
                        ("code", models.CharField(blank=True, db_index=True, help_text="Kode wilayah resmi jika tersedia.", max_length=50)),
                        ("administrative_level", models.CharField(choices=[("country", "Negara"), ("province", "Provinsi"), ("regency", "Kabupaten"), ("city", "Kota"), ("district", "Kecamatan"), ("village", "Desa/Kelurahan"), ("other", "Lainnya")], db_index=True, max_length=20)),
                        ("latitude", models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                        ("longitude", models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                        ("country_code", models.CharField(default="ID", max_length=2)),
                        ("is_active", models.BooleanField(db_index=True, default=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="children", to="locations.location")),
                    ],
                    options={
                        "db_table": "entities_location",
                        "ordering": ["administrative_level", "name"],
                    },
                ),
                migrations.AddIndex(
                    model_name="location",
                    index=models.Index(fields=["administrative_level", "name"], name="location_level_name_idx"),
                ),
                migrations.AddConstraint(
                    model_name="location",
                    constraint=models.UniqueConstraint(fields=("name", "administrative_level", "parent", "country_code"), name="unique_location_hierarchy"),
                ),
                migrations.CreateModel(
                    name="LocationAlias",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("alias", models.CharField(max_length=200)),
                        ("language", models.CharField(default="id", max_length=20)),
                        ("is_active", models.BooleanField(db_index=True, default=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("location", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="aliases", to="locations.location")),
                    ],
                    options={
                        "db_table": "entities_locationalias",
                        "ordering": ["alias"],
                        "constraints": [models.UniqueConstraint(fields=("location", "alias"), name="unique_location_alias")],
                    },
                ),
            ],
        ),
    ]
