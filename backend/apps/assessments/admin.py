from django.contrib import admin

from .models import (
    EarlyWarning,
    EarlyWarningHistory,
    InformationEvaluation,
    InformationGap,
    IntelligenceRecommendation,
    IntelligenceRecommendationHistory,
    IntelligenceReport,
    IntelligenceReportHistory,
    IntelligenceReportSection,
    SignalAssessment,
    SourceEvaluation,
)


class EarlyWarningHistoryInline(admin.TabularInline):
    model = EarlyWarningHistory
    extra = 0
    readonly_fields = (
        "action",
        "from_status",
        "to_status",
        "notes",
        "changed_by",
        "changed_at",
    )
    can_delete = False


class IntelligenceRecommendationHistoryInline(admin.TabularInline):
    model = IntelligenceRecommendationHistory
    extra = 0
    readonly_fields = (
        "action",
        "from_status",
        "to_status",
        "notes",
        "changed_by",
        "changed_at",
    )
    can_delete = False


class IntelligenceReportSectionInline(admin.StackedInline):
    model = IntelligenceReportSection
    extra = 0
    readonly_fields = (
        "signal",
        "disease",
        "assessment_version",
        "warning_version",
        "recommendation_version",
        "created_at",
        "updated_at",
    )


class IntelligenceReportHistoryInline(admin.TabularInline):
    model = IntelligenceReportHistory
    extra = 0
    readonly_fields = (
        "action",
        "from_status",
        "to_status",
        "notes",
        "metadata",
        "changed_by",
        "changed_at",
    )
    can_delete = False


class InformationGapInline(admin.TabularInline):
    model = InformationGap
    extra = 1


@admin.register(SourceEvaluation)
class SourceEvaluationAdmin(admin.ModelAdmin):
    list_display = (
        "signal",
        "source",
        "historical_accuracy_score",
        "authority_score",
        "transparency_score",
        "independence_score",
        "reliability_score",
        "reliability_level",
        "evaluated_by",
        "evaluated_at",
    )

    list_filter = (
        "reliability_level",
        "source__source_type",
        "evaluated_at",
    )

    search_fields = (
        "signal__code",
        "signal__title",
        "source__name",
        "notes",
    )

    list_select_related = (
        "signal",
        "source",
        "evaluated_by",
    )

    readonly_fields = (
        "id",
        "reliability_score",
        "reliability_level",
        "evaluated_at",
        "updated_at",
    )


@admin.register(InformationEvaluation)
class InformationEvaluationAdmin(admin.ModelAdmin):
    list_display = (
        "signal",
        "article",
        "corroboration_score",
        "consistency_score",
        "specificity_score",
        "timeliness_score",
        "credibility_score",
        "credibility_level",
        "supports_signal",
        "evaluated_by",
    )

    list_filter = (
        "credibility_level",
        "supports_signal",
        "evaluated_at",
    )

    search_fields = (
        "signal__code",
        "signal__title",
        "article__title",
        "notes",
        "contradiction_notes",
    )

    list_select_related = (
        "signal",
        "article",
        "article__source",
        "evaluated_by",
    )

    readonly_fields = (
        "id",
        "credibility_score",
        "credibility_level",
        "evaluated_at",
        "updated_at",
    )


@admin.register(SignalAssessment)
class SignalAssessmentAdmin(admin.ModelAdmin):
    list_display = (
        "signal",
        "version",
        "is_current",
        "status",
        "priority_score",
        "recommended_priority",
        "confidence_score",
        "recommended_confidence",
        "assessed_by",
        "completed_at",
    )

    list_filter = (
        "is_current",
        "status",
        "recommended_priority",
        "recommended_confidence",
        "completed_at",
    )

    search_fields = (
        "signal__code",
        "signal__title",
        "analytical_judgement",
        "implications",
        "recommended_actions",
    )

    list_select_related = (
        "signal",
        "assessed_by",
    )

    readonly_fields = (
        "id",
        "version",
        "priority_score",
        "confidence_score",
        "recommended_priority",
        "recommended_confidence",
        "assessed_at",
        "completed_at",
        "created_at",
        "updated_at",
    )

    inlines = [
        InformationGapInline,
    ]


@admin.register(InformationGap)
class InformationGapAdmin(admin.ModelAdmin):
    list_display = (
        "assessment",
        "gap_type",
        "description",
        "priority",
        "status",
        "resolved_by",
        "resolved_at",
    )

    list_filter = (
        "gap_type",
        "priority",
        "status",
    )

    search_fields = (
        "assessment__signal__code",
        "description",
        "resolution_notes",
    )

    list_select_related = (
        "assessment",
        "assessment__signal",
        "resolved_by",
    )


@admin.register(EarlyWarning)
class EarlyWarningAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "signal",
        "version",
        "level",
        "confidence_level",
        "status",
        "is_current",
        "issued_by",
        "issued_at",
    )
    list_filter = (
        "level",
        "confidence_level",
        "status",
        "is_current",
        "issued_at",
    )
    search_fields = (
        "code",
        "title",
        "signal__code",
        "signal__title",
        "summary",
    )
    list_select_related = (
        "signal",
        "assessment",
        "issued_by",
        "closed_by",
    )
    readonly_fields = (
        "id",
        "code",
        "signal",
        "assessment",
        "version",
        "level",
        "confidence_level",
        "status",
        "is_current",
        "issued_by",
        "issued_at",
        "closed_by",
        "closed_at",
        "created_at",
        "updated_at",
    )
    inlines = [EarlyWarningHistoryInline]


@admin.register(IntelligenceRecommendation)
class IntelligenceRecommendationAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "signal",
        "version",
        "action_category",
        "urgency",
        "status",
        "is_current",
        "target_unit",
        "due_date",
    )
    list_filter = (
        "action_category",
        "urgency",
        "status",
        "is_current",
        "due_date",
    )
    search_fields = (
        "code",
        "title",
        "signal__code",
        "signal__title",
        "target_unit",
        "recommended_action",
    )
    list_select_related = (
        "signal",
        "assessment",
        "early_warning",
        "created_by",
        "approved_by",
        "completed_by",
    )
    readonly_fields = (
        "id",
        "code",
        "signal",
        "assessment",
        "early_warning",
        "version",
        "status",
        "is_current",
        "created_by",
        "approved_by",
        "approved_at",
        "completed_by",
        "completed_at",
        "created_at",
        "updated_at",
    )
    inlines = [IntelligenceRecommendationHistoryInline]


@admin.register(IntelligenceReport)
class IntelligenceReportAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "hal",
        "report_date",
        "status",
        "created_by",
        "finalized_by",
        "distributed_at",
        "archived_at",
    )
    list_filter = ("status", "report_date", "finalized_at", "distributed_at")
    search_fields = ("code", "hal", "kepada", "dari", "tembusan")
    list_select_related = (
        "created_by",
        "updated_by",
        "finalized_by",
        "distributed_by",
        "archived_by",
    )
    readonly_fields = (
        "id",
        "code",
        "status",
        "created_by",
        "updated_by",
        "finalized_by",
        "finalized_at",
        "distributed_by",
        "distributed_at",
        "archived_by",
        "archived_at",
        "created_at",
        "updated_at",
    )
    inlines = [IntelligenceReportSectionInline, IntelligenceReportHistoryInline]
