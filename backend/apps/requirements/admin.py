from django.contrib import admin

from .models import (
    IntelligenceRequirement,
    RequirementDisease,
    RequirementArticle,
    RequirementCollectionSession,
    RequirementHistory,
    RequirementIndicator,
    RequirementInformationGap,
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
        "status",
        "requirement_type",
        "priority",
        "valid_from",
        "valid_until",
        "is_active",
        "assigned_to",
    )

    list_filter = (
        "status",
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
        "activated_at",
        "answered_at",
        "closed_at",
    )

    inlines = [
        RequirementKeywordInline,
        RequirementDiseaseInline,
        RequirementLocationInline,
        RequirementIndicatorInline,
    ]


@admin.register(RequirementCollectionSession)
class RequirementCollectionSessionAdmin(admin.ModelAdmin):
    list_display = ("requirement", "session", "linked_by", "linked_at")
    search_fields = ("requirement__code", "requirement__title")
    list_select_related = ("requirement", "session", "linked_by")


@admin.register(RequirementArticle)
class RequirementArticleAdmin(admin.ModelAdmin):
    list_display = (
        "requirement",
        "article",
        "link_source",
        "relevance_score",
        "linked_at",
    )
    list_filter = ("link_source",)
    search_fields = (
        "requirement__code",
        "requirement__title",
        "article__title",
    )
    list_select_related = ("requirement", "article", "linked_by")


@admin.register(RequirementInformationGap)
class RequirementInformationGapAdmin(admin.ModelAdmin):
    list_display = ("requirement", "priority", "status", "created_at")
    list_filter = ("priority", "status")
    search_fields = ("requirement__code", "description", "resolution_notes")
    list_select_related = ("requirement", "created_by", "resolved_by")


@admin.register(RequirementHistory)
class RequirementHistoryAdmin(admin.ModelAdmin):
    list_display = ("requirement", "action", "changed_by", "changed_at")
    list_filter = ("action", "to_status")
    search_fields = ("requirement__code", "notes")
    readonly_fields = (
        "requirement",
        "action",
        "from_status",
        "to_status",
        "notes",
        "metadata",
        "changed_by",
        "changed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


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
