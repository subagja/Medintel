from django.contrib import admin

from .models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    DiseaseAlias,
    Location,
)


class DiseaseAliasInline(admin.TabularInline):
    model = DiseaseAlias
    extra = 1


@admin.register(Disease)
class DiseaseAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "canonical_name",
        "category",
        "is_priority",
        "is_active",
    )

    list_filter = (
        "category",
        "is_priority",
        "is_active",
    )

    search_fields = (
        "name",
        "canonical_name",
        "code",
        "aliases__alias",
    )

    prepopulated_fields = {
        "code": ("name",),
    }

    inlines = [
        DiseaseAliasInline,
    ]


@admin.register(DiseaseAlias)
class DiseaseAliasAdmin(admin.ModelAdmin):
    list_display = (
        "alias",
        "disease",
        "language",
        "is_active",
    )

    list_filter = (
        "language",
        "is_active",
    )

    search_fields = (
        "alias",
        "disease__name",
    )

    list_select_related = (
        "disease",
    )


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "administrative_level",
        "parent",
        "code",
        "country_code",
        "is_active",
    )

    list_filter = (
        "administrative_level",
        "country_code",
        "is_active",
    )

    search_fields = (
        "name",
        "code",
        "parent__name",
    )

    list_select_related = (
        "parent",
    )


@admin.register(ArticleDisease)
class ArticleDiseaseAdmin(admin.ModelAdmin):
    list_display = (
        "article",
        "disease",
        "mention_text",
        "confidence_score",
        "is_primary",
        "validation_status",
    )

    list_filter = (
        "validation_status",
        "extraction_method",
        "is_primary",
        "disease",
    )

    search_fields = (
        "article__title",
        "disease__name",
        "mention_text",
    )

    list_select_related = (
        "article",
        "disease",
        "validated_by",
    )


@admin.register(ArticleLocation)
class ArticleLocationAdmin(admin.ModelAdmin):
    list_display = (
        "article",
        "location",
        "mention_text",
        "confidence_score",
        "is_primary",
        "validation_status",
    )

    list_filter = (
        "validation_status",
        "extraction_method",
        "is_primary",
        "location__administrative_level",
    )

    search_fields = (
        "article__title",
        "location__name",
        "mention_text",
    )

    list_select_related = (
        "article",
        "location",
        "validated_by",
    )


@admin.register(ArticleFact)
class ArticleFactAdmin(admin.ModelAdmin):
    list_display = (
        "article",
        "disease",
        "location",
        "event_date",
        "case_count",
        "death_count",
        "trend",
        "validation_status",
    )

    list_filter = (
        "trend",
        "validation_status",
        "extraction_method",
        "event_date",
    )

    search_fields = (
        "article__title",
        "disease__name",
        "location__name",
        "fact_text",
    )

    list_select_related = (
        "article",
        "disease",
        "location",
        "validated_by",
    )