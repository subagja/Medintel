import re
import uuid
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models


domain_validator = RegexValidator(
    regex=r"^[a-z0-9.-]+\.[a-z]{2,}$",
    message=(
        "Masukkan domain tanpa protokol atau path, "
        "misalnya kompas.com."
    ),
)


class Source(models.Model):
    class SourceType(models.TextChoices):
        GOVERNMENT = (
            "government",
            "Instansi Pemerintah",
        )
        NATIONAL_MEDIA = (
            "national_media",
            "Media Nasional",
        )
        LOCAL_MEDIA = (
            "local_media",
            "Media Lokal",
        )
        INTERNATIONAL_MEDIA = (
            "international_media",
            "Media Internasional",
        )
        HEALTH_ORGANIZATION = (
            "health_organization",
            "Organisasi Kesehatan",
        )
        RESEARCH_INSTITUTION = (
            "research_institution",
            "Lembaga Penelitian",
        )
        OTHER = (
            "other",
            "Lainnya",
        )

    class CrawlStrategy(models.TextChoices):
        HTML = (
            "html",
            "HTML Listing",
        )
        RSS = (
            "rss",
            "RSS/Atom",
        )
        SITEMAP = (
            "sitemap",
            "XML Sitemap",
        )
        MANUAL = (
            "manual",
            "Manual Only",
        )

    name = models.CharField(
        max_length=150,
        unique=True,
    )

    code = models.SlugField(
        max_length=50,
        unique=True,
        help_text=(
            "Kode internal sumber, misalnya "
            "kompas atau kemkes."
        ),
    )

    domain = models.CharField(
        max_length=255,
        unique=True,
        validators=[domain_validator],
        help_text=(
            "Domain tanpa https://, www, atau path."
        ),
    )

    base_url = models.URLField(
        max_length=1000,
    )

    source_type = models.CharField(
        max_length=30,
        choices=SourceType.choices,
        db_index=True,
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

    crawl_enabled = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            "Aktifkan agar sumber dapat diproses "
            "oleh crawler otomatis."
        ),
    )

    crawl_strategy = models.CharField(
        max_length=20,
        choices=CrawlStrategy.choices,
        default=CrawlStrategy.HTML,
        db_index=True,
    )

    allow_subdomains = models.BooleanField(
        default=False,
        help_text=(
            "Izinkan crawler mengambil URL "
            "dari subdomain sumber."
        ),
    )

    max_articles_per_run = models.PositiveIntegerField(
        default=50,
        help_text=(
            "Jumlah maksimum artikel yang diproses "
            "dalam satu eksekusi crawler."
        ),
    )

    request_delay_seconds = models.FloatField(
        default=1.0,
        help_text=(
            "Jeda minimum antarpermintaan HTTP "
            "dalam detik."
        ),
    )

    request_timeout_seconds = (
        models.PositiveSmallIntegerField(
            default=20,
            help_text=(
                "Batas waktu setiap permintaan HTTP "
                "dalam detik."
            ),
        )
    )

    user_agent = models.CharField(
        max_length=300,
        blank=True,
        help_text=(
            "User-Agent khusus sumber. Kosongkan "
            "untuk memakai User-Agent default."
        ),
    )

    last_crawled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    crawler_notes = models.TextField(
        blank=True,
        help_text=(
            "Catatan teknis mengenai struktur halaman "
            "atau pembatasan crawler."
        ),
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
                fields=[
                    "is_verified",
                    "is_active",
                ],
                name="source_verified_active_idx",
            ),
            models.Index(
                fields=[
                    "crawl_enabled",
                    "crawl_strategy",
                ],
                name="source_crawl_strategy_idx",
            ),
        ]

    def clean(self) -> None:
        super().clean()

        errors = {}

        domain = self.domain.strip().lower()

        if domain.startswith(
            (
                "http://",
                "https://",
            )
        ):
            domain = urlsplit(domain).netloc

        domain = domain.split(":")[0]
        domain = domain.removeprefix("www.")
        domain = domain.rstrip(".")

        self.domain = domain

        parsed_base_url = urlsplit(
            self.base_url
        )

        base_hostname = (
            parsed_base_url.hostname or ""
        )

        normalized_base_hostname = (
            base_hostname.lower()
            .removeprefix("www.")
            .rstrip(".")
        )

        if domain and normalized_base_hostname:
            domain_matches = (
                normalized_base_hostname == domain
                or (
                    self.allow_subdomains
                    and normalized_base_hostname.endswith(
                        f".{domain}"
                    )
                )
            )

            if not domain_matches:
                errors["base_url"] = (
                    "Domain pada base URL tidak sesuai "
                    "dengan domain sumber."
                )

        if self.crawl_enabled and not self.is_active:
            errors["crawl_enabled"] = (
                "Crawler tidak dapat diaktifkan "
                "pada sumber yang tidak aktif."
            )

        if (
            self.crawl_enabled
            and not self.is_verified
        ):
            errors["crawl_enabled"] = (
                "Crawler hanya dapat diaktifkan "
                "pada sumber yang telah diverifikasi."
            )

        if self.max_articles_per_run < 1:
            errors["max_articles_per_run"] = (
                "Jumlah artikel minimal adalah 1."
            )

        if self.max_articles_per_run > 1000:
            errors["max_articles_per_run"] = (
                "Jumlah artikel maksimal per eksekusi "
                "adalah 1.000."
            )

        if self.request_delay_seconds < 0:
            errors["request_delay_seconds"] = (
                "Jeda permintaan tidak boleh negatif."
            )

        if self.request_timeout_seconds < 1:
            errors["request_timeout_seconds"] = (
                "Timeout minimal adalah 1 detik."
            )

        if errors:
            raise ValidationError(errors)

    def save(
        self,
        *args,
        **kwargs,
    ):
        self.full_clean()

        return super().save(
            *args,
            **kwargs,
        )

    def __str__(self) -> str:
        return self.name


class SourceUrlPattern(models.Model):
    class PatternType(models.TextChoices):
        ALLOW = (
            "allow",
            "Izinkan",
        )
        DENY = (
            "deny",
            "Tolak",
        )

    class MatchType(models.TextChoices):
        PREFIX = (
            "prefix",
            "Awalan URL",
        )
        CONTAINS = (
            "contains",
            "Mengandung",
        )
        REGEX = (
            "regex",
            "Regular Expression",
        )

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        related_name="url_patterns",
    )

    pattern_type = models.CharField(
        max_length=10,
        choices=PatternType.choices,
        db_index=True,
    )

    match_type = models.CharField(
        max_length=20,
        choices=MatchType.choices,
        default=MatchType.PREFIX,
        db_index=True,
    )

    pattern = models.CharField(
        max_length=1000,
        help_text=(
            "Bisa berupa path, URL penuh, "
            "potongan teks, atau regular expression."
        ),
    )

    priority = models.PositiveSmallIntegerField(
        default=100,
        help_text=(
            "Angka lebih kecil dievaluasi lebih dahulu."
        ),
    )

    description = models.CharField(
        max_length=300,
        blank=True,
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
        ordering = [
            "priority",
            "pattern_type",
            "pattern",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "source",
                    "pattern_type",
                    "match_type",
                    "pattern",
                ],
                name="unique_source_url_pattern",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "source",
                    "pattern_type",
                    "is_active",
                ],
                name="source_pattern_active_idx",
            ),
        ]

    def clean(self) -> None:
        super().clean()

        self.pattern = self.pattern.strip()

        if not self.pattern:
            raise ValidationError(
                {
                    "pattern": (
                        "Pola URL tidak boleh kosong."
                    ),
                }
            )

        if (
            self.match_type
            == self.MatchType.REGEX
        ):
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValidationError(
                    {
                        "pattern": (
                            "Regular expression "
                            f"tidak valid: {exc}"
                        ),
                    }
                ) from exc

    def save(
        self,
        *args,
        **kwargs,
    ):
        self.full_clean()

        return super().save(
            *args,
            **kwargs,
        )

    def __str__(self) -> str:
        return (
            f"{self.source.code} — "
            f"{self.pattern_type}: "
            f"{self.pattern}"
        )


class SourceSeedUrl(models.Model):
    class SeedType(models.TextChoices):
        LISTING = (
            "listing",
            "Halaman Daftar Artikel",
        )
        RSS = (
            "rss",
            "RSS/Atom Feed",
        )
        SITEMAP = (
            "sitemap",
            "XML Sitemap",
        )
        DIRECT = (
            "direct",
            "URL Artikel Langsung",
        )

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        related_name="seed_urls",
    )

    url = models.URLField(
        max_length=1000,
    )

    seed_type = models.CharField(
        max_length=20,
        choices=SeedType.choices,
        default=SeedType.LISTING,
        db_index=True,
    )

    priority = models.PositiveSmallIntegerField(
        default=100,
        help_text=(
            "Angka lebih kecil diproses lebih dahulu."
        ),
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    notes = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "priority",
            "url",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "source",
                    "url",
                ],
                name="unique_source_seed_url",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "source",
                    "seed_type",
                    "is_active",
                ],
                name="source_seed_active_idx",
            ),
        ]

    def clean(self) -> None:
        super().clean()

        if not self.source_id or not self.url:
            return

        parsed_url = urlsplit(
            self.url
        )

        hostname = parsed_url.hostname

        if not hostname:
            raise ValidationError(
                {
                    "url": (
                        "URL awal tidak memiliki "
                        "domain yang valid."
                    ),
                }
            )

        normalized_hostname = (
            hostname.lower()
            .removeprefix("www.")
            .rstrip(".")
        )

        source_domain = (
            self.source.domain.lower()
            .removeprefix("www.")
            .rstrip(".")
        )

        domain_matches = (
            normalized_hostname == source_domain
            or (
                self.source.allow_subdomains
                and normalized_hostname.endswith(
                    f".{source_domain}"
                )
            )
        )

        if not domain_matches:
            raise ValidationError(
                {
                    "url": (
                        "Domain URL awal tidak sesuai "
                        "dengan domain sumber."
                    ),
                }
            )

    def save(
        self,
        *args,
        **kwargs,
    ):
        self.full_clean()

        return super().save(
            *args,
            **kwargs,
        )

    def __str__(self) -> str:
        return (
            f"{self.source.code} — "
            f"{self.url}"
        )