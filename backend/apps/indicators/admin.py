from django.contrib import admin

from .models import (
    Indicator,
    IndicatorEvidence,
    IndicatorType,
)


class IndicatorEvidenceInline(admin.TabularInline):
    model = IndicatorEvidence
    extra = 0

    fields = (
        "article",
        "article_fact",
        "evidence_text",
        "confidence_score",
        "is_primary_evidence",
    )

    readonly_fields = fields

    show_change_link = True


@admin.register(IndicatorType)
class IndicatorTypeAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "category",
        "default_weight",
        "requires_analyst_validation",
        "is_active",
    )

    list_filter = (
        "category",
        "requires_analyst_validation",
        "is_active",
    )

    search_fields = (
        "code",
        "name",
        "description",
    )


@admin.register(Indicator)
class IndicatorAdmin(admin.ModelAdmin):
    list_display = (
        "indicator_type",
        "disease",
        "location",
        "event_date",
        "value",
        "unit",
        "direction",
        "confidence_score",
        "status",
    )

    list_filter = (
        "indicator_type",
        "status",
        "direction",
        "event_date",
        "created_by_system",
    )

    search_fields = (
        "summary",
        "disease__name",
        "location__name",
        "indicator_type__name",
    )

    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "indicator_type",
        "disease",
        "location",
        "validated_by",
    )

    inlines = [
        IndicatorEvidenceInline,
    ]


@admin.register(IndicatorEvidence)
class IndicatorEvidenceAdmin(admin.ModelAdmin):
    list_display = (
        "indicator",
        "article",
        "article_fact",
        "confidence_score",
        "is_primary_evidence",
        "created_at",
    )

    list_filter = (
        "is_primary_evidence",
        "created_at",
    )

    search_fields = (
        "indicator__summary",
        "article__title",
        "evidence_text",
    )

    list_select_related = (
        "indicator",
        "article",
        "article_fact",
    )