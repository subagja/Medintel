import uuid

from django.db import models


class Location(models.Model):
    """Data wilayah administratif (negara, provinsi, kabupaten/kota, dst).

    Dipindahkan dari `apps.entities` agar referensi geografis terpisah dari
    logika ekstraksi entitas artikel.
    """

    class AdministrativeLevel(models.TextChoices):
        COUNTRY = "country", "Negara"
        PROVINCE = "province", "Provinsi"
        REGENCY = "regency", "Kabupaten"
        CITY = "city", "Kota"
        DISTRICT = "district", "Kecamatan"
        VILLAGE = "village", "Desa/Kelurahan"
        OTHER = "other", "Lainnya"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    name = models.CharField(
        max_length=200,
    )

    code = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
        help_text="Kode wilayah resmi jika tersedia.",
    )

    administrative_level = models.CharField(
        max_length=20,
        choices=AdministrativeLevel.choices,
        db_index=True,
    )

    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )

    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )

    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )

    country_code = models.CharField(
        max_length=2,
        default="ID",
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        # db_table dikunci ke nama lama supaya tabel fisik di database TIDAK
        # perlu di-drop/re-create saat model ini pindah app.
        db_table = "entities_location"
        ordering = [
            "administrative_level",
            "name",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "name",
                    "administrative_level",
                    "parent",
                    "country_code",
                ],
                name="unique_location_hierarchy",
            ),
        ]
        indexes = [
            models.Index(
                fields=["administrative_level", "name"],
                name="location_level_name_idx",
            ),
        ]

    def __str__(self) -> str:
        if self.parent:
            return f"{self.name}, {self.parent.name}"

        return self.name


class LocationAlias(models.Model):
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="aliases",
    )

    alias = models.CharField(
        max_length=200,
    )

    language = models.CharField(
        max_length=20,
        default="id",
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        db_table = "entities_locationalias"
        ordering = ["alias"]
        constraints = [
            models.UniqueConstraint(
                fields=["location", "alias"],
                name="unique_location_alias",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.alias} → {self.location.name}"
