from django.contrib import admin

from .models import (
    IntelligenceRequirement,
    RequirementDisease,
    RequirementIndicator,
    RequirementKeyword,
    RequirementLocation,
)


class RequirementKeywordInline(admin.TabularInline):
    model = RequirementKeyword
    extra = 1


class RequirementDiseaseInline(admin.TabularInline):
    model = RequirementDisease
    extra = 1
    autocomplete_fields = (
        "disease",
    )


class RequirementLocationInline(admin.TabularInline):
    model = RequirementLocation
    extra = 1
    autocomplete_fields = (
        "location",
    )


class RequirementIndicatorInline(admin.TabularInline):
    model = RequirementIndicator
    extra = 0
    readonly_fields = (
        "indicator",
        "relevance_score",
        "relevance_reason",
        "matched_by",
        "reviewed_by",
        "reviewed_at",
        "created_at",
    )

    can_delete = False


@admin.register(IntelligenceRequirement)
class IntelligenceRequirementAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "title",
        "requirement_type",
        "priority",
        "valid_from",
        "valid_until",
        "is_active",
    )

    list_filter = (
        "requirement_type",
        "priority",
        "is_active",
        "valid_from",
        "valid_until",
    )

    search_fields = (
        "code",
        "title",
        "description",
        "keywords__keyword",
    )

    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
    )

    inlines = [
        RequirementKeywordInline,
        RequirementDiseaseInline,
        RequirementLocationInline,
        RequirementIndicatorInline,
    ]


@admin.register(RequirementKeyword)
class RequirementKeywordAdmin(admin.ModelAdmin):
    list_display = (
        "keyword",
        "requirement",
        "keyword_type",
        "weight",
        "is_active",
    )

    list_filter = (
        "keyword_type",
        "is_active",
    )

    search_fields = (
        "keyword",
        "requirement__code",
        "requirement__title",
    )

    list_select_related = (
        "requirement",
    )


@admin.register(RequirementDisease)
class RequirementDiseaseAdmin(admin.ModelAdmin):
    list_display = (
        "requirement",
        "disease",
        "priority_weight",
        "is_primary",
    )

    list_filter = (
        "is_primary",
    )

    search_fields = (
        "requirement__code",
        "requirement__title",
        "disease__name",
    )

    autocomplete_fields = (
        "requirement",
        "disease",
    )


@admin.register(RequirementLocation)
class RequirementLocationAdmin(admin.ModelAdmin):
    list_display = (
        "requirement",
        "location",
        "priority_weight",
        "include_descendants",
        "is_primary",
    )

    list_filter = (
        "include_descendants",
        "is_primary",
        "location__administrative_level",
    )

    search_fields = (
        "requirement__code",
        "requirement__title",
        "location__name",
    )

    autocomplete_fields = (
        "requirement",
        "location",
    )


@admin.register(RequirementIndicator)
class RequirementIndicatorAdmin(admin.ModelAdmin):
    list_display = (
        "requirement",
        "indicator",
        "relevance_score",
        "matched_by",
        "reviewed_by",
        "reviewed_at",
    )

    list_filter = (
        "matched_by",
        "reviewed_at",
        "requirement__priority",
    )

    search_fields = (
        "requirement__code",
        "requirement__title",
        "indicator__summary",
        "relevance_reason",        
	    "summary",
	    "validation_notes",
	    "disease__name",
	    "location__name",
	    "indicator_type__name",
    )

    list_select_related = (
        "requirement",
        "indicator",
        "indicator__indicator_type",
        "reviewed_by",
    )