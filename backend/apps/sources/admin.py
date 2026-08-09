from django.contrib import admin

from .models import (
    Source,
    SourceDiscoveryQuery,
    SourceSeedUrl,
    SourceUrlPattern,
)
from .services import (
    check_source_crawl_readiness,
)


class SourceSeedUrlInline(
    admin.TabularInline
):
    model = SourceSeedUrl
    extra = 1

    fields = (
        "url",
        "seed_type",
        "priority",
        "is_active",
        "notes",
    )

    ordering = (
        "priority",
        "url",
    )


class SourceUrlPatternInline(
    admin.TabularInline
):
    model = SourceUrlPattern
    extra = 1

    fields = (
        "pattern_type",
        "match_type",
        "pattern",
        "priority",
        "description",
        "is_active",
    )

    ordering = (
        "priority",
        "pattern_type",
        "pattern",
    )


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "domain",
        "source_type",
        "is_verified",
        "is_active",
        "crawl_enabled",
        "crawl_strategy",
        "crawler_ready",
        "last_crawled_at",
    )

    list_filter = (
        "source_type",
        "is_verified",
        "is_active",
        "crawl_enabled",
        "crawl_strategy",
        "allow_subdomains",
    )

    search_fields = (
        "name",
        "code",
        "domain",
        "base_url",
        "verification_notes",
        "crawler_notes",
    )

    prepopulated_fields = {
        "code": (
            "name",
        ),
    }

    readonly_fields = (
        "verified_at",
        "last_crawled_at",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Identitas Sumber",
            {
                "fields": (
                    "name",
                    "code",
                    "source_type",
                ),
            },
        ),
        (
            "Domain dan Verifikasi",
            {
                "fields": (
                    "domain",
                    "base_url",
                    "allow_subdomains",
                    "is_verified",
                    "is_active",
                    "verification_notes",
                    "verified_at",
                ),
            },
        ),
        (
            "Konfigurasi Crawling",
            {
                "fields": (
                    "crawl_enabled",
                    "crawl_strategy",
                    "max_articles_per_run",
                    "request_delay_seconds",
                    "request_timeout_seconds",
                    "user_agent",
                    "last_crawled_at",
                    "crawler_notes",
                ),
            },
        ),
        (
            "Audit",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                ),
                "classes": (
                    "collapse",
                ),
            },
        ),
    )

    inlines = (
        SourceSeedUrlInline,
        SourceUrlPatternInline,
    )

    @admin.display(
        boolean=True,
        description="Crawler Ready",
    )
    def crawler_ready(
        self,
        obj: Source,
    ) -> bool:
        readiness = (
            check_source_crawl_readiness(
                obj
            )
        )

        return readiness.is_ready


@admin.register(SourceSeedUrl)
class SourceSeedUrlAdmin(
    admin.ModelAdmin
):
    list_display = (
        "source",
        "url",
        "seed_type",
        "priority",
        "is_active",
        "updated_at",
    )

    list_filter = (
        "seed_type",
        "is_active",
        "source__source_type",
    )

    search_fields = (
        "source__name",
        "source__code",
        "source__domain",
        "url",
        "notes",
    )

    list_select_related = (
        "source",
    )

    ordering = (
        "source",
        "priority",
        "url",
    )


@admin.register(SourceUrlPattern)
class SourceUrlPatternAdmin(
    admin.ModelAdmin
):
    list_display = (
        "source",
        "pattern",
        "pattern_type",
        "match_type",
        "priority",
        "is_active",
    )

    list_filter = (
        "pattern_type",
        "match_type",
        "is_active",
        "source__source_type",
    )

    search_fields = (
        "source__name",
        "source__code",
        "source__domain",
        "pattern",
        "description",
    )

    list_select_related = (
        "source",
    )

    ordering = (
        "source",
        "priority",
        "pattern_type",
        "pattern",
    )


@admin.register(SourceDiscoveryQuery)
class SourceDiscoveryQueryAdmin(admin.ModelAdmin):
    list_display = (
        "source",
        "provider",
        "query",
        "language",
        "country",
        "max_age_days",
        "priority",
        "is_active",
    )
    list_filter = (
        "provider",
        "is_active",
        "language",
        "country",
        "source__source_type",
    )
    search_fields = (
        "source__name",
        "source__code",
        "source__domain",
        "query",
        "notes",
    )
    list_select_related = ("source",)
    ordering = ("source", "priority", "provider", "query")
    readonly_fields = (
        "source",
        "provider",
        "query",
        "language",
        "country",
        "max_age_days",
        "priority",
        "is_active",
        "notes",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
