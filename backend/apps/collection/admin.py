from django.contrib import admin

from .models import CollectionJob, CollectionJobItem


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


@admin.register(CollectionJob)
class CollectionJobAdmin(admin.ModelAdmin):
    list_display = (
        "source",
        "job_type",
        "crawler_name",
        "status",
        "total_found",
        "total_created",
        "total_duplicate",
        "total_rejected",
        "total_failed",
        "started_at",
        "finished_at",
    )

    list_filter = (
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
        "source",
        "triggered_by",
    )

    inlines = [
        CollectionJobItemInline,
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