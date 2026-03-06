# from django.db import models
# from django.utils import timezone


# class Source(models.Model):
#     name = models.CharField(max_length=200)
#     base_url = models.URLField(blank=True, null=True)
#     rss_url = models.URLField(blank=True, null=True)
#     country_code = models.CharField(max_length=8, default="ID")
#     is_active = models.BooleanField(default=True)
#     created_at = models.DateTimeField(default=timezone.now)

#     def __str__(self):
#         return self.name


# class Location(models.Model):
#     LEVEL_CHOICES = [
#         ("country", "Country"),
#         ("province", "Province"),
#         ("kabupaten", "Kabupaten"),
#         ("kota", "Kota"),
#         ("kecamatan", "Kecamatan"),
#         ("desa", "Desa"),
#         ("facility", "Facility"),
#         ("unknown", "Unknown"),
#     ]

#     name = models.CharField(max_length=200)
#     level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="unknown")
#     parent = models.ForeignKey("self", on_delete=models.SET_NULL, blank=True, null=True, related_name="children")
#     country_code = models.CharField(max_length=8, default="ID")

#     lat = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
#     lon = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)

#     geocode_provider = models.CharField(max_length=50, blank=True, null=True)   # Nominatim/Manual
#     geocode_quality = models.CharField(max_length=20, default="unknown")        # exact/approx/centroid/unknown

#     is_active = models.BooleanField(default=True)
#     created_at = models.DateTimeField(default=timezone.now)
#     updated_at = models.DateTimeField(auto_now=True)

#     class Meta:
#         indexes = [
#             models.Index(fields=["name"]),
#             models.Index(fields=["level"]),
#         ]

#     def __str__(self):
#         return f"{self.name} ({self.level})"


# class LocationAlias(models.Model):
#     location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="aliases")
#     alias_text = models.CharField(max_length=200)
#     alias_norm = models.CharField(max_length=220, db_index=True)
#     created_at = models.DateTimeField(default=timezone.now)

#     def __str__(self):
#         return f"{self.alias_text} -> {self.location.name}"


# class Signal(models.Model):
#     STATUS_CHOICES = [
#         ("raw", "Raw"),
#         ("triaged", "Triaged"),
#         ("validated", "Validated"),
#         ("rejected", "Rejected"),
#     ]

#     source = models.ForeignKey(Source, on_delete=models.SET_NULL, null=True, blank=True, related_name="signals")

#     disease_tag = models.CharField(max_length=100, db_index=True)
#     title = models.TextField()
#     url = models.URLField()
#     final_url = models.URLField(blank=True, null=True)

#     published_at = models.DateTimeField(blank=True, null=True)
#     crawled_at = models.DateTimeField(default=timezone.now)

#     summary = models.TextField(blank=True, null=True)
#     content_text = models.TextField(blank=True, null=True)

#     threat_score = models.IntegerField(default=0)
#     status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="raw")

#     dedup_hash = models.CharField(max_length=64, unique=True)
#     language = models.CharField(max_length=10, default="id")

#     detected_diseases = models.TextField(blank=True, null=True)
#     event_types = models.TextField(blank=True, null=True)
#     severity_nlp = models.IntegerField(default=0)

#     created_at = models.DateTimeField(default=timezone.now)

#     class Meta:
#         indexes = [
#             models.Index(fields=["disease_tag"]),
#             models.Index(fields=["threat_score"]),
#             models.Index(fields=["status"]),
#             models.Index(fields=["published_at"]),
#         ]

#     def __str__(self):
#         t = self.title or ""
#         return t if len(t) <= 70 else t[:67] + "..."


# class SignalLocation(models.Model):
#     METHOD_CHOICES = [
#         ("gazetteer", "Gazetteer"),
#         ("regex", "Regex"),
#         ("ner", "NER"),
#         ("manual", "Manual"),
#     ]
#     GEO_STATUS_CHOICES = [
#         ("ok", "OK"),
#         ("not_found", "Not Found"),
#         ("empty", "Empty"),
#         ("skip_noise", "Skip Noise"),
#         ("skip_general", "Skip General"),
#         ("net_err", "Network Error"),
#         ("timeout", "Timeout"),
#         ("rate_limit", "Rate Limit"),
#         ("service_err", "Service Error"),
#     ]

#     signal = models.ForeignKey(Signal, on_delete=models.CASCADE, related_name="signal_locations")
#     location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name="signal_locations")

#     raw_location_text = models.CharField(max_length=250, blank=True, null=True)
#     method = models.CharField(max_length=20, choices=METHOD_CHOICES, default="gazetteer")
#     confidence = models.FloatField(default=0.0)

#     geocode_status = models.CharField(max_length=20, choices=GEO_STATUS_CHOICES, default="empty")
#     lat = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
#     lon = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)

#     is_primary = models.BooleanField(default=True)
#     created_at = models.DateTimeField(default=timezone.now)

#     class Meta:
#         indexes = [
#             models.Index(fields=["geocode_status"]),
#             models.Index(fields=["method"]),
#             models.Index(fields=["is_primary"]),
#         ]

#     def __str__(self):
#         return f"{self.signal_id} -> {self.raw_location_text or '-'}"

from django.db import models
from django.db.models import Q
from django.utils import timezone


class Source(models.Model):
    name = models.CharField(max_length=200, unique=True)
    base_url = models.URLField(blank=True, null=True)
    rss_url = models.URLField(blank=True, null=True)
    country_code = models.CharField(max_length=8, default="ID")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["country_code"]),
        ]

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
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="children"
    )
    country_code = models.CharField(max_length=8, default="ID")

    lat = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    lon = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)

    geocode_provider = models.CharField(max_length=50, blank=True, null=True)   # Nominatim/Manual
    geocode_quality = models.CharField(max_length=20, default="unknown")        # exact/approx/centroid/unknown

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["level", "name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["level"]),
            models.Index(fields=["country_code"]),
            models.Index(fields=["parent"]),
            models.Index(fields=["is_active"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "level", "parent", "country_code"],
                name="uq_location_name_level_parent_country"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.level})"


class LocationAlias(models.Model):
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="aliases")
    alias_text = models.CharField(max_length=200)
    alias_norm = models.CharField(max_length=220, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["alias_text"]
        indexes = [
            models.Index(fields=["alias_norm"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["location", "alias_norm"],
                name="uq_location_alias_norm_per_location"
            )
        ]

    def __str__(self):
        return f"{self.alias_text} -> {self.location.name}"


class Signal(models.Model):
    STATUS_CHOICES = [
        ("raw", "Raw"),
        ("triaged", "Triaged"),
        ("validated", "Validated"),
        ("rejected", "Rejected"),
    ]

    source = models.ForeignKey(
        Source,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="signals"
    )

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

    detected_diseases = models.TextField(blank=True, null=True)
    event_types = models.TextField(blank=True, null=True)
    severity_nlp = models.IntegerField(default=0)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-published_at", "-crawled_at", "-created_at"]
        indexes = [
            models.Index(fields=["disease_tag"]),
            models.Index(fields=["threat_score"]),
            models.Index(fields=["status"]),
            models.Index(fields=["published_at"]),
            models.Index(fields=["crawled_at"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["severity_nlp"]),
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
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="signal_locations"
    )

    raw_location_text = models.CharField(max_length=250, blank=True, null=True)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default="gazetteer")
    confidence = models.FloatField(default=0.0)

    geocode_status = models.CharField(max_length=20, choices=GEO_STATUS_CHOICES, default="empty")
    lat = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    lon = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)

    is_primary = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["geocode_status"]),
            models.Index(fields=["method"]),
            models.Index(fields=["is_primary"]),
            models.Index(fields=["location"]),
            models.Index(fields=["signal"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["signal"],
                condition=Q(is_primary=True),
                name="uq_one_primary_location_per_signal"
            )
        ]

    def __str__(self):
        return f"{self.signal_id} -> {self.raw_location_text or '-'}"