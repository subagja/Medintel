from django.db import models
from django.utils import timezone


class Source(models.Model):
    name = models.CharField(max_length=200)
    base_url = models.URLField(blank=True, null=True)
    rss_url = models.URLField(blank=True, null=True)
    country_code = models.CharField(max_length=8, default="ID")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name


class Location(models.Model):
    LEVEL_CHOICES = [
        ("country", "Country"),
        ("province", "Province"),
        ("kabupaten", "Kabupaten"),
        ("kota", "Kota"),
        ("kecamatan", "Kecamatan"),
        ("desa", "Desa"),
        ("facility", "Facility"),
        ("unknown", "Unknown"),
    ]

    name = models.CharField(max_length=200)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="unknown")
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, blank=True, null=True, related_name="children")
    country_code = models.CharField(max_length=8, default="ID")

    lat = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    lon = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)

    geocode_provider = models.CharField(max_length=50, blank=True, null=True)   # Nominatim/Manual
    geocode_quality = models.CharField(max_length=20, default="unknown")        # exact/approx/centroid/unknown

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["level"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.level})"


class LocationAlias(models.Model):
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="aliases")
    alias_text = models.CharField(max_length=200)
    alias_norm = models.CharField(max_length=220, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.alias_text} -> {self.location.name}"


class Signal(models.Model):
    STATUS_CHOICES = [
        ("raw", "Raw"),
        ("triaged", "Triaged"),
        ("validated", "Validated"),
        ("rejected", "Rejected"),
    ]

    source = models.ForeignKey(Source, on_delete=models.SET_NULL, null=True, blank=True, related_name="signals")

    disease_tag = models.CharField(max_length=100, db_index=True)
    title = models.TextField()
    url = models.URLField()
    final_url = models.URLField(blank=True, null=True)

    published_at = models.DateTimeField(blank=True, null=True)
    crawled_at = models.DateTimeField(default=timezone.now)

    summary = models.TextField(blank=True, null=True)
    content_text = models.TextField(blank=True, null=True)

    threat_score = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="raw")

    dedup_hash = models.CharField(max_length=64, unique=True)
    language = models.CharField(max_length=10, default="id")

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["disease_tag"]),
            models.Index(fields=["threat_score"]),
            models.Index(fields=["status"]),
            models.Index(fields=["published_at"]),
        ]

    def __str__(self):
        t = self.title or ""
        return t if len(t) <= 70 else t[:67] + "..."


class SignalLocation(models.Model):
    METHOD_CHOICES = [
        ("gazetteer", "Gazetteer"),
        ("regex", "Regex"),
        ("ner", "NER"),
        ("manual", "Manual"),
    ]
    GEO_STATUS_CHOICES = [
        ("ok", "OK"),
        ("not_found", "Not Found"),
        ("empty", "Empty"),
        ("skip_noise", "Skip Noise"),
        ("skip_general", "Skip General"),
        ("net_err", "Network Error"),
        ("timeout", "Timeout"),
        ("rate_limit", "Rate Limit"),
        ("service_err", "Service Error"),
    ]

    signal = models.ForeignKey(Signal, on_delete=models.CASCADE, related_name="signal_locations")
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name="signal_locations")

    raw_location_text = models.CharField(max_length=250, blank=True, null=True)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default="gazetteer")
    confidence = models.FloatField(default=0.0)

    geocode_status = models.CharField(max_length=20, choices=GEO_STATUS_CHOICES, default="empty")
    lat = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    lon = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)

    is_primary = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["geocode_status"]),
            models.Index(fields=["method"]),
            models.Index(fields=["is_primary"]),
        ]

    def __str__(self):
        return f"{self.signal_id} -> {self.raw_location_text or '-'}"