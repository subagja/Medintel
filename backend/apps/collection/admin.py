from django.contrib import admin

from .models import (
    CollectionJob,
    CollectionJobItem,
    CollectionJobLog,
    CollectionSchedule,
    CollectionSession,
    CollectionWorker,
)


class CollectionJobInline(admin.TabularInline):
    model = CollectionJob
    extra = 0
    fields = (
        "source",
        "job_type",
        "status",
        "total_created",
        "total_rejected",
        "total_failed",
    )
    readonly_fields = fields
    show_change_link = True


@admin.register(CollectionSession)
class CollectionSessionAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "scope",
        "selected_source",
        "include_google_news",
        "html_deep_scan",
        "planned_job_count",
        "triggered_by",
        "created_at",
    )
    list_filter = (
        "scope",
        "include_google_news",
        "html_deep_scan",
        "created_at",
    )
    search_fields = (
        "id",
        "selected_source__name",
        "selected_source__code",
        "triggered_by__username",
    )
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [CollectionJobInline]


class CollectionJobItemInline(admin.TabularInline):
    model = CollectionJobItem
    extra = 0
    fields = (
        "title",
        "original_url",
        "status",
        "article",
        "processed_at",
    )
    readonly_fields = fields
    show_change_link = True


class CollectionJobLogInline(admin.TabularInline):
    model = CollectionJobLog
    extra = 0
    fields = ("created_at", "level", "event", "message")
    readonly_fields = fields


@admin.register(CollectionJob)
class CollectionJobAdmin(admin.ModelAdmin):
    list_display = (
        "session",
        "source",
        "job_type",
        "crawler_name",
        "status",
        "attempt_count",
        "max_attempts",
        "worker_id",
        "total_found",
        "total_created",
        "total_duplicate",
        "total_rejected",
        "total_failed",
        "started_at",
        "finished_at",
    )

    list_filter = (
        "session",
        "job_type",
        "status",
        "source",
        "trigger_type",
        "created_at",
    )

    search_fields = (
        "source__name",
        "source__code",
        "crawler_name",
        "error_message",
    )

    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "session",
        "source",
        "triggered_by",
    )

    inlines = [
        CollectionJobItemInline,
        CollectionJobLogInline,
    ]


@admin.register(CollectionJobItem)
class CollectionJobItemAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "collection_job",
        "status",
        "article",
        "processed_at",
    )

    list_filter = (
        "status",
        "collection_job__source",
        "created_at",
    )

    search_fields = (
        "title",
        "original_url",
        "normalized_url",
        "reason",
        "error_message",
    )

    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "collection_job",
        "collection_job__source",
        "article",
    )


@admin.register(CollectionSchedule)
class CollectionScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "source",
        "recurrence",
        "is_active",
        "next_run_at",
        "last_run_at",
        "last_session",
    )
    list_filter = ("recurrence", "is_active", "include_google_news")
    search_fields = ("name", "source__name", "source__code")
    list_select_related = ("source", "last_session", "created_by")


@admin.register(CollectionWorker)
class CollectionWorkerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "hostname",
        "process_id",
        "status",
        "concurrency",
        "last_heartbeat_at",
    )
    list_filter = ("status",)
    readonly_fields = (
        "id",
        "hostname",
        "process_id",
        "started_at",
        "last_heartbeat_at",
        "stopped_at",
        "metadata",
    )


@admin.register(CollectionJobLog)
class CollectionJobLogAdmin(admin.ModelAdmin):
    list_display = ("job", "level", "event", "created_at")
    list_filter = ("level", "event", "created_at")
    search_fields = ("job__id", "message")
    readonly_fields = ("job", "level", "event", "message", "metadata", "created_at")
