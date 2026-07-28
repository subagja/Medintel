from django.db import models

# Create your models here.
from django.core.validators import RegexValidator
from django.db import models
from urllib.parse import urlsplit

domain_validator = RegexValidator(
    regex=r"^[a-z0-9.-]+\.[a-z]{2,}$",
    message="Masukkan domain tanpa protokol atau path, misalnya kompas.com.",
)


class Source(models.Model):
    class SourceType(models.TextChoices):
        GOVERNMENT = "government", "Instansi Pemerintah"
        NATIONAL_MEDIA = "national_media", "Media Nasional"
        LOCAL_MEDIA = "local_media", "Media Lokal"
        INTERNATIONAL_MEDIA = (
            "international_media",
            "Media Internasional",
        )
        OTHER = "other", "Lainnya"

    name = models.CharField(
        max_length=150,
        unique=True,
    )

    code = models.SlugField(
        max_length=50,
        unique=True,
        help_text="Kode internal sumber, misalnya kompas atau kemkes.",
    )

    domain = models.CharField(
        max_length=255,
        unique=True,
        validators=[domain_validator],
        help_text="Domain tanpa https://, www, atau path.",
    )

    base_url = models.URLField()

    source_type = models.CharField(
        max_length=30,
        choices=SourceType.choices,
    )

    is_verified = models.BooleanField(
        default=False,
        db_index=True,
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    verification_notes = models.TextField(
        blank=True,
    )

    verified_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(
                fields=["is_verified", "is_active"],
                name="source_verified_active_idx",
            ),
        ]

    def clean(self) -> None:
        super().clean()

        domain = self.domain.strip().lower()

        if domain.startswith(("http://", "https://")):
            domain = urlsplit(domain).netloc

        domain = domain.split(":")[0]
        domain = domain.removeprefix("www.")
        domain = domain.rstrip(".")

        self.domain = domain

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class SourceUrlPattern(models.Model):
    class PatternType(models.TextChoices):
        ALLOW = "allow", "Diizinkan"
        DENY = "deny", "Ditolak"

    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        related_name="url_patterns",
    )

    pattern = models.CharField(
        max_length=500,
        help_text=(
            "Pola URL atau path, misalnya /read/, /search/, "
            "atau ekspresi reguler."
        ),
    )

    pattern_type = models.CharField(
        max_length=10,
        choices=PatternType.choices,
    )

    is_regex = models.BooleanField(
        default=False,
        help_text="Aktifkan apabila pattern menggunakan regular expression.",
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    description = models.CharField(
        max_length=255,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["source", "pattern_type", "pattern"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "source",
                    "pattern",
                    "pattern_type",
                ],
                name="unique_source_url_pattern",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.source.name} - "
            f"{self.get_pattern_type_display()} - "
            f"{self.pattern}"
        )