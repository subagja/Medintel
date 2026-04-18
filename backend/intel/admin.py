from django.contrib import admin
from .models import (
    Source,
    Location,
    LocationAlias,
    Signal,
    SignalLocation,
    ScoringRule,
    SystemSetting,
    Alert,
    AuditLog,
)


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "country_code", "is_active", "created_at")
    search_fields = ("name", "base_url", "rss_url")
    list_filter = ("is_active", "country_code")


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "display_name",
        "name",
        "level",
        "parent",
        "province_code",
        "city_regency_code",
        "is_active",
        "is_false_positive",
    )
    search_fields = ("display_name", "name", "normalized_name")
    list_filter = ("level", "is_active", "is_false_positive", "country_code")
    autocomplete_fields = ("parent",)


@admin.register(LocationAlias)
class LocationAliasAdmin(admin.ModelAdmin):
    list_display = ("id", "alias", "location", "is_primary", "is_active")
    search_fields = ("alias", "normalized_alias", "location__display_name", "location__name")
    list_filter = ("is_primary", "is_active")
    autocomplete_fields = ("location",)


class SignalLocationInline(admin.TabularInline):
    model = SignalLocation
    extra = 0
    autocomplete_fields = ("location",)


@admin.register(Signal)
class SignalAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "disease_tag",
        "threat_score",
        "status",
        "geocode_status",
        "approved_for_mapping",
        "is_high_risk",
        "published_at",
    )
    search_fields = ("title", "source_url", "raw_location_text", "disease_tag")
    list_filter = ("status", "geocode_status", "approved_for_mapping", "is_high_risk", "disease_tag")
    autocomplete_fields = ("source", "validated_by")
    inlines = [SignalLocationInline]


@admin.register(SignalLocation)
class SignalLocationAdmin(admin.ModelAdmin):
    list_display = ("id", "signal", "location", "raw_location_text", "confidence", "method", "is_primary")
    search_fields = ("raw_location_text", "signal__title", "location__display_name")
    list_filter = ("method", "is_primary")
    autocomplete_fields = ("signal", "location")


@admin.register(ScoringRule)
class ScoringRuleAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "rule_type", "keyword", "weight", "is_active")
    search_fields = ("name", "keyword")
    list_filter = ("rule_type", "is_active")

@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("id", "key", "value", "is_active", "updated_at")
    search_fields = ("key", "value", "description")
    list_filter = ("is_active",)
    
@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "action", "model_name", "object_id", "user", "created_at")
    search_fields = ("model_name", "object_id", "notes")
    list_filter = ("action", "model_name")
    autocomplete_fields = ("user",)

@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "alert_type",
        "title",
        "location",
        "signal_count",
        "avg_score",
        "status",
        "created_at",
    )
    search_fields = ("title", "description", "dedup_key", "rule_key")
    list_filter = ("alert_type", "status")
    autocomplete_fields = ("location",)