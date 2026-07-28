from django.contrib import admin

# Register your models here.
from django.contrib import admin

from .models import Source, SourceUrlPattern


class SourceUrlPatternInline(admin.TabularInline):
    model = SourceUrlPattern
    extra = 1


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "domain",
        "source_type",
        "is_verified",
        "is_active",
        "verified_at",
    )

    list_filter = (
        "source_type",
        "is_verified",
        "is_active",
    )

    search_fields = (
        "name",
        "code",
        "domain",
    )

    prepopulated_fields = {
        "code": ("name",),
    }

    inlines = [
        SourceUrlPatternInline,
    ]


@admin.register(SourceUrlPattern)
class SourceUrlPatternAdmin(admin.ModelAdmin):
    list_display = (
        "source",
        "pattern",
        "pattern_type",
        "is_regex",
        "is_active",
    )

    list_filter = (
        "pattern_type",
        "is_regex",
        "is_active",
    )

    search_fields = (
        "source__name",
        "source__domain",
        "pattern",
    )