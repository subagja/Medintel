from django.contrib import admin

from .models import (
    Indicator,
    IndicatorEvidence,
    IndicatorReviewLog,
    IndicatorType,
)
from .services import (
    reject_indicator,
    validate_indicator,
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
        "validated_by",
        "validated_at",
    )

    list_filter = (
        "indicator_type",
        "status",
        "direction",
        "event_date",
        "created_by_system",
        "validated_at",
    )

    search_fields = (
        "summary",
        "validation_notes",
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

    actions = [
        "validate_selected_indicators",
        "reject_selected_indicators",
    ]

    @admin.action(
        description="Validasi indikator terpilih"
    )
    def validate_selected_indicators(
        self,
        request,
        queryset,
    ):
        validated_count = 0
        skipped_count = 0

        for indicator in queryset:
            if indicator.status == Indicator.Status.REJECTED:
                skipped_count += 1
                continue

            validate_indicator(
                indicator=indicator,
                reviewer=request.user,
                notes="Indikator divalidasi melalui Django Admin.",
            )

            validated_count += 1

        self.message_user(
            request,
            (
                f"{validated_count} indikator berhasil divalidasi. "
                f"{skipped_count} indikator dilewati."
            ),
        )


    @admin.action(
        description="Tolak indikator terpilih"
    )
    def reject_selected_indicators(
        self,
        request,
        queryset,
    ):
        rejected_count = 0

        for indicator in queryset:
            reject_indicator(
                indicator=indicator,
                reviewer=request.user,
                notes="Indikator ditolak melalui Django Admin.",
            )

            rejected_count += 1

        self.message_user(
            request,
            f"{rejected_count} indikator berhasil ditolak.",
        )


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


@admin.register(IndicatorReviewLog)
class IndicatorReviewLogAdmin(admin.ModelAdmin):
    list_display = (
        "indicator",
        "action",
        "reviewer",
        "reviewed_at",
    )

    list_filter = (
        "action",
        "reviewed_at",
        "indicator__indicator_type",
    )

    search_fields = (
        "indicator__summary",
        "reviewer__username",
        "notes",
    )

    readonly_fields = (
        "id",
        "indicator",
        "action",
        "reviewer",
        "before_data",
        "after_data",
        "notes",
        "reviewed_at",
    )

    list_select_related = (
        "indicator",
        "indicator__indicator_type",
        "reviewer",
    )

    def has_add_permission(
        self,
        request,
    ) -> bool:
        return False

    def has_change_permission(
        self,
        request,
        obj=None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request,
        obj=None,
    ) -> bool:
        return False