from django.contrib import admin
from django.utils.html import format_html

from .models import Source, Location, LocationAlias, Signal, SignalLocation


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("name", "country_code", "is_active", "rss_url")
    search_fields = ("name", "base_url", "rss_url")
    list_filter = ("country_code", "is_active")


class LocationAliasInline(admin.TabularInline):
    model = LocationAlias
    extra = 1


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "level", "country_code", "parent", "lat", "lon", "geocode_quality", "is_active")
    search_fields = ("name",)
    list_filter = ("level", "country_code", "geocode_quality", "is_active")
    inlines = [LocationAliasInline]


class SignalLocationInline(admin.TabularInline):
    model = SignalLocation
    extra = 0


@admin.register(Signal)
class SignalAdmin(admin.ModelAdmin):
    list_display = ("id", "disease_tag", "threat_score", "status", "published_at", "source")
    search_fields = ("title", "url", "final_url", "summary")
    list_filter = ("disease_tag", "status", "source")
    ordering = ("-published_at", "-crawled_at")
    inlines = [SignalLocationInline]


# =========================
# SignalLocation "Error Center"
# =========================
@admin.action(description="Set Signal.status = TRIAGED (selected)")
def action_set_triaged(modeladmin, request, queryset):
    sig_ids = queryset.values_list("signal_id", flat=True).distinct()
    Signal.objects.filter(id__in=sig_ids).update(status="triaged")


@admin.action(description="Set Signal.status = REJECTED (selected)")
def action_set_rejected(modeladmin, request, queryset):
    sig_ids = queryset.values_list("signal_id", flat=True).distinct()
    Signal.objects.filter(id__in=sig_ids).update(status="rejected")


@admin.action(description="Set Signal.status = VALIDATED (selected)")
def action_set_validated(modeladmin, request, queryset):
    sig_ids = queryset.values_list("signal_id", flat=True).distinct()
    Signal.objects.filter(id__in=sig_ids).update(status="validated")


@admin.action(description="Mark selected SignalLocation as NOT primary (is_primary=False)")
def action_unset_primary(modeladmin, request, queryset):
    queryset.update(is_primary=False)


@admin.register(SignalLocation)
class SignalLocationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "signal_link",
        "signal_title_short",
        "disease_tag",
        "raw_location_text",
        "location",
        "geocode_status",
        "method",
        "confidence",
        "lat",
        "lon",
        "is_primary",
        "map_link",
    )
    search_fields = ("raw_location_text", "signal__title", "signal__url", "signal__final_url")
    list_filter = ("geocode_status", "method", "is_primary", "signal__disease_tag", "signal__status")
    list_select_related = ("signal", "location", "signal__source")
    ordering = ("-signal__published_at", "-signal__crawled_at")
    actions = [action_set_triaged, action_set_rejected, action_set_validated, action_unset_primary]

    # Quick computed columns
    @admin.display(description="Signal")
    def signal_link(self, obj):
        if not obj.signal_id:
            return "-"
        return format_html('<a href="/admin/intel/signal/{}/change/">#{}</a>', obj.signal_id, obj.signal_id)

    @admin.display(description="Judul (ringkas)")
    def signal_title_short(self, obj):
        t = (obj.signal.title or "").strip()
        if len(t) > 90:
            t = t[:87] + "..."
        return t or "-"

    @admin.display(description="Tag")
    def disease_tag(self, obj):
        return obj.signal.disease_tag if obj.signal_id else "-"

    @admin.display(description="Map")
    def map_link(self, obj):
        try:
            if obj.lat is None or obj.lon is None:
                return "-"
            return format_html(
                '<a target="_blank" href="https://www.openstreetmap.org/?mlat={}&mlon={}#map=12/{}/{}">OSM</a>',
                obj.lat, obj.lon, obj.lat, obj.lon
            )
        except Exception:
            return "-"