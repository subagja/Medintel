import re
import unicodedata
from django.conf import settings
from django.db import models

def normalize_region_code(value: str) -> str:
    if not value:
        return ""

    value = str(value).strip().lower()
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.replace("/", " ")
    value = re.sub(r"[^a-z0-9\s_-]", "", value)
    value = re.sub(r"[\s\-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Source(TimeStampedModel):
    name = models.CharField(max_length=200, unique=True)
    base_url = models.URLField(blank=True, default="")
    rss_url = models.URLField(blank=True, default="")
    country_code = models.CharField(max_length=10, default="ID", db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Location(TimeStampedModel):
    LEVEL_CHOICES = [
        ("country", "Country"),
        ("province", "Province"),
        ("regency", "Regency"),
        ("city", "City"),
        ("district", "District"),
        ("village", "Village"),
        ("other", "Other"),
    ]

    name = models.CharField(max_length=255, db_index=True)
    display_name = models.CharField(max_length=255, blank=True, default="")
    normalized_name = models.CharField(max_length=255, db_index=True, blank=True, default="")
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="other", db_index=True)

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children"
    )

    country_code = models.CharField(max_length=10, default="ID", db_index=True)
    province_code = models.CharField(max_length=20, blank=True, default="", db_index=True)
    city_regency_code = models.CharField(max_length=20, blank=True, default="", db_index=True)

    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_false_positive = models.BooleanField(default=False)

    class Meta:
        ordering = ["level", "display_name", "name"]
        indexes = [
            models.Index(fields=["normalized_name"]),
            models.Index(fields=["level"]),
            models.Index(fields=["province_code"]),
            models.Index(fields=["city_regency_code"]),
            models.Index(fields=["is_active"]),
        ]

    # def save(self, *args, **kwargs):
    #     if not self.display_name:
    #         self.display_name = (self.name or "").strip()
    #     if not self.normalized_name:
    #         self.normalized_name = (self.name or "").strip().lower()
    #     super().save(*args, **kwargs)

    def save(self, *args, **kwargs):
        if not self.display_name:
            self.display_name = (self.name or "").strip()

        if not self.normalized_name:
            self.normalized_name = normalize_region_code(self.name)

        if self.level == "province":
            if not self.province_code:
                self.province_code = normalize_region_code(self.display_name or self.name)

        if self.level in ["city", "regency"]:
            if not self.city_regency_code:
                self.city_regency_code = normalize_region_code(self.display_name or self.name)

            if not self.province_code and self.parent and self.parent.level == "province":
                self.province_code = self.parent.province_code or normalize_region_code(self.parent.display_name or self.parent.name)

        super().save(*args, **kwargs)

    def __str__(self):
        return self.display_name or self.name


class LocationAlias(TimeStampedModel):
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="aliases")
    alias = models.CharField(max_length=255, db_index=True)
    normalized_alias = models.CharField(max_length=255, db_index=True, blank=True, default="")
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["alias"]
        unique_together = ("location", "alias")
        indexes = [
            models.Index(fields=["normalized_alias"]),
            models.Index(fields=["is_active"]),
        ]

    def save(self, *args, **kwargs):
        if not self.normalized_alias:
            self.normalized_alias = (self.alias or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.alias} -> {self.location}"

class PublisherDomainAlias(TimeStampedModel):
    alias = models.CharField(max_length=255, unique=True)
    normalized_alias = models.CharField(max_length=255, db_index=True)
    domain = models.CharField(max_length=255, db_index=True)

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["alias"]

    def __str__(self):
        return f"{self.alias} -> {self.domain}"

class Signal(TimeStampedModel):
    STATUS_CHOICES = [
        ("raw", "Raw"),
        ("validated", "Validated"),
        ("noise", "Noise"),
        ("approved", "Approved"),
    ]

    GEOCODE_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("ok", "OK"),
        ("matched", "Matched"),
        ("gazetteer_only", "Gazetteer Only"),
        ("manual", "Manual"),

        ("empty_loc", "Empty Location"),
        ("not_found", "Not Found"),
        ("net_err", "Network Error"),
        ("skip_noise", "Skip Noise"),
        ("skip_too_general", "Skip Too General"),
        ("skip_low_conf", "Skip Low Confidence"),

        # Legacy uppercase compatibility
        ("OK", "OK"),
        ("EMPTY_LOC", "EMPTY_LOC"),
        ("NOT_FOUND", "NOT_FOUND"),
        ("NET_ERR", "NET_ERR"),
        ("SKIP_NOISE", "SKIP_NOISE"),
        ("SKIP_TOO_GENERAL", "SKIP_TOO_GENERAL"),
        ("SKIP_LOW_CONF", "SKIP_LOW_CONF"),
        ("MANUAL", "MANUAL"),
        ("PENDING", "PENDING"),
    ]

    title = models.CharField(max_length=500, db_index=True)
    content = models.TextField(blank=True, default="")
    source = models.ForeignKey(Source, null=True, blank=True, on_delete=models.SET_NULL, related_name="signals")

    source_url = models.URLField(unique=True)
    published_at = models.DateTimeField(null=True, blank=True)
    crawled_at = models.DateTimeField(null=True, blank=True)

    admin_province = models.CharField(max_length=150, blank=True, default="")
    admin_kabkota = models.CharField(max_length=150, blank=True, default="")
    location_level = models.CharField(max_length=50, blank=True, default="")

    scoring_reason = models.TextField(blank=True, default="")
    scoring_breakdown = models.JSONField(blank=True, default=dict)
    risk_level = models.CharField(max_length=20, blank=True, default="")

    disease_tag = models.CharField(max_length=100, blank=True, default="", db_index=True)
    threat_score = models.IntegerField(default=0, db_index=True)

    assessment_status = models.CharField(max_length=30, blank=True, default="")
    assessment_summary = models.TextField(blank=True, default="")
    assessment_5w1h = models.JSONField(blank=True, default=dict)
    assessment_source_text = models.TextField(blank=True, default="")
    assessment_error = models.TextField(blank=True, default="")
    assessment_generated_at = models.DateTimeField(null=True, blank=True)

    source_url = models.URLField(max_length=1000, blank=True, default="")          # URL awal dari crawler, bisa Google News
    resolved_url = models.URLField(max_length=1000, blank=True, default="")
    url_resolution_status = models.CharField(max_length=40, blank=True, default="")
    url_resolution_method = models.CharField(max_length=60, blank=True, default="")
    url_resolution_error = models.TextField(blank=True, default="")
    raw_location_text = models.CharField(max_length=255, blank=True, default="", db_index=True)
    geocode_status = models.CharField(
        max_length=30,
        choices=GEOCODE_STATUS_CHOICES,
        default="pending",
        db_index=True,
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="raw", db_index=True)
    validation_notes = models.TextField(blank=True, default="")
    is_high_risk = models.BooleanField(default=False, db_index=True)
    approved_for_mapping = models.BooleanField(default=False, db_index=True)

    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="validated_signals"
    )
    validated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["disease_tag"]),
            models.Index(fields=["geocode_status"]),
            models.Index(fields=["threat_score"]),
            models.Index(fields=["approved_for_mapping"]),
            models.Index(fields=["is_high_risk"]),
        ]

    def save(self, *args, **kwargs):
        self.is_high_risk = self.threat_score > 50
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title[:100]

class ResolvedSourceURL(models.Model):
    original_url = models.URLField(max_length=1000, unique=True)
    resolved_url = models.URLField(max_length=1000)
    source_name = models.CharField(max_length=255, blank=True, default="")
    title = models.TextField(blank=True, default="")
    method = models.CharField(max_length=60, blank=True, default="")
    confidence = models.FloatField(default=0.0)
    is_manual = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class SignalLocation(TimeStampedModel):
    METHOD_CHOICES = [
        ("auto", "Auto"),
        ("manual", "Manual"),
        ("gazetteer", "Gazetteer"),
        ("alias", "Alias"),
        ("location_exact", "Location Exact"),
        ("location_alias", "Location Alias"),
        ("legacy_admin_normalized", "Legacy Admin Normalized"),
        ("legacy_admin_province_normalized", "Legacy Admin Province Normalized"),
    ]

    signal = models.ForeignKey(
        Signal,
        on_delete=models.CASCADE,
        related_name="locations",
    )
    location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signal_links",
    )

    raw_location_text = models.CharField(max_length=255, blank=True, default="")
    confidence = models.FloatField(null=True, blank=True)
    method = models.CharField(max_length=50, choices=METHOD_CHOICES, default="auto")
    is_primary = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_primary", "-created_at"]

    def __str__(self):
        loc_name = self.location.display_name if self.location else "No Location"
        return f"{self.signal_id} -> {loc_name}"


class ScoringRule(TimeStampedModel):
    RULE_TYPE_CHOICES = [
        ("keyword", "Keyword"),
        ("disease", "Disease"),
        ("location", "Location"),
        ("custom", "Custom"),
    ]

    name = models.CharField(max_length=200)
    rule_type = models.CharField(max_length=30, choices=RULE_TYPE_CHOICES, default="keyword")
    keyword = models.CharField(max_length=255, blank=True, default="")
    weight = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.weight})"

class SystemSetting(TimeStampedModel):
    key = models.CharField(max_length=100, unique=True, db_index=True)
    value = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return f"{self.key} = {self.value}"

class Alert(TimeStampedModel):
    STATUS_CHOICES = [
        ("open", "Open"),
        ("reviewed", "Reviewed"),
        ("closed", "Closed"),
    ]

    ALERT_TYPE_CHOICES = [
        ("cluster_city_48h", "Cluster in City 48h"),
        ("high_avg_score", "High Average Score"),
        ("new_location", "New Location"),
    ]

    alert_type = models.CharField(max_length=50, choices=ALERT_TYPE_CHOICES, db_index=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="alerts"
    )

    signal_count = models.IntegerField(default=0)
    avg_score = models.FloatField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open", db_index=True)

    first_signal_at = models.DateTimeField(null=True, blank=True)
    last_signal_at = models.DateTimeField(null=True, blank=True)

    rule_key = models.CharField(max_length=100, blank=True, default="", db_index=True)
    dedup_key = models.CharField(max_length=255, unique=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
        
class AuditLog(TimeStampedModel):
    ACTION_CHOICES = [
        ("create", "Create"),
        ("update", "Update"),
        ("delete", "Delete"),
        ("validate", "Validate"),
        ("approve", "Approve"),
        ("mark_noise", "Mark Noise"),
        ("manual_edit", "Manual Edit"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs"
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES, db_index=True)
    model_name = models.CharField(max_length=100, db_index=True)
    object_id = models.CharField(max_length=50, db_index=True)
    notes = models.TextField(blank=True, default="")
    before_data = models.JSONField(null=True, blank=True)
    after_data = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} - {self.model_name} - {self.object_id}"