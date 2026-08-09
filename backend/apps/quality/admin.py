from django.contrib import admin

from .models import EvaluationHistory, EvaluationRecord, QualityMetricSnapshot


@admin.register(QualityMetricSnapshot)
class QualityMetricSnapshotAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "period_start", "period_end", "created_at")
    search_fields = ("code", "title")
    readonly_fields = ("created_at",)


@admin.register(EvaluationRecord)
class EvaluationRecordAdmin(admin.ModelAdmin):
    list_display = (
        "code", "evaluation_type", "evaluator_name", "evaluation_date", "status",
    )
    list_filter = ("evaluation_type", "status", "evaluation_date")
    search_fields = ("code", "evaluator_name", "institution")


@admin.register(EvaluationHistory)
class EvaluationHistoryAdmin(admin.ModelAdmin):
    list_display = ("evaluation", "action", "changed_by", "changed_at")
    list_filter = ("action",)
    readonly_fields = ("changed_at",)

