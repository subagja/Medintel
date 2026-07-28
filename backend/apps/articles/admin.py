from django.contrib import admin

from .models import Article


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "source",
        "published_at",
        "processing_status",
        "crawled_at",
    )

    list_filter = (
        "source",
        "processing_status",
        "published_at",
        "crawled_at",
    )

    search_fields = (
        "title",
        "original_url",
        "normalized_url",
        "content_text",
        "content_hash",
    )

    readonly_fields = (
        "id",
        "content_hash",
        "crawled_at",
        "created_at",
        "updated_at",
    )

    date_hierarchy = "published_at"

    ordering = (
        "-published_at",
        "-crawled_at",
    )

    list_select_related = (
        "source",
    )