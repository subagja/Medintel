from __future__ import annotations

from collections import Counter
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from apps.signals.models import Signal, SignalHistory

from ..models import (
    EarlyWarning,
    IntelligenceRecommendation,
    SignalAssessment,
)
from .disease_priority import build_disease_priority_dataset
from .threat_map import (
    LEVEL_SEVERITY,
    active_warning_queryset,
    build_threat_map_dataset,
)


ACTIVE_SIGNAL_STATUSES = {
    Signal.Status.VALIDATED,
    Signal.Status.CORRECTED,
    Signal.Status.ESCALATED,
}

ANALYSABLE_SIGNAL_STATUSES = ACTIVE_SIGNAL_STATUSES | {
    Signal.Status.CLOSED,
}

ACTIVE_RECOMMENDATION_STATUSES = {
    IntelligenceRecommendation.Status.APPROVED,
    IntelligenceRecommendation.Status.IN_PROGRESS,
}

OPEN_RECOMMENDATION_STATUSES = ACTIVE_RECOMMENDATION_STATUSES | {
    IntelligenceRecommendation.Status.DRAFT,
}

WARNING_PRESENTATION = {
    EarlyWarning.Level.MONITORING: {
        "label": "Pemantauan",
        "badge": "bg-blue-lt",
        "accent": "blue",
    },
    EarlyWarning.Level.ADVISORY: {
        "label": "Waspada",
        "badge": "bg-yellow-lt",
        "accent": "yellow",
    },
    EarlyWarning.Level.HIGH: {
        "label": "Tinggi",
        "badge": "bg-orange-lt",
        "accent": "orange",
    },
    EarlyWarning.Level.CRITICAL: {
        "label": "Kritis",
        "badge": "bg-danger text-white",
        "accent": "red",
    },
}

URGENCY_SEVERITY = {
    IntelligenceRecommendation.Urgency.ROUTINE: 1,
    IntelligenceRecommendation.Urgency.PRIORITY: 2,
    IntelligenceRecommendation.Urgency.URGENT: 3,
    IntelligenceRecommendation.Urgency.IMMEDIATE: 4,
}


def _recommendation_queryset():
    return (
        IntelligenceRecommendation.objects.filter(
            is_current=True,
            status__in=OPEN_RECOMMENDATION_STATUSES,
        )
        .select_related(
            "signal__primary_disease",
            "signal__primary_location",
            "assessment",
            "early_warning",
        )
        .order_by("due_date", "-updated_at")
    )


def _recommendation_url(recommendation) -> str:
    return (
        f"{reverse('dashboard:intelligence-recommendation')}"
        f"?assessment={recommendation.assessment_id}"
    )


def _recommendation_action(recommendation, *, overdue: bool) -> dict:
    if recommendation.status == IntelligenceRecommendation.Status.DRAFT:
        return {
            "key": "decision",
            "label": "Perlu ditetapkan analis",
            "badge": "bg-yellow-lt",
        }
    if overdue:
        return {
            "key": "overdue",
            "label": "Lewat tenggat",
            "badge": "bg-danger-lt",
        }
    if (
        recommendation.status
        == IntelligenceRecommendation.Status.APPROVED
    ):
        return {
            "key": "start",
            "label": "Belum mulai tindak lanjut",
            "badge": "bg-blue-lt",
        }
    return {
        "key": "monitor",
        "label": "Sedang ditindaklanjuti",
        "badge": "bg-azure-lt",
    }


def _recommendation_rows(recommendations, *, as_of_date):
    rows = []
    for recommendation in recommendations:
        overdue = bool(
            recommendation.due_date
            and recommendation.due_date < as_of_date
            and recommendation.status in ACTIVE_RECOMMENDATION_STATUSES
        )
        action = _recommendation_action(
            recommendation,
            overdue=overdue,
        )
        rows.append(
            {
                "object": recommendation,
                "code": recommendation.code,
                "title": recommendation.title,
                "status": recommendation.status,
                "status_label": recommendation.get_status_display(),
                "urgency": recommendation.urgency,
                "urgency_label": recommendation.get_urgency_display(),
                "category_label": (
                    recommendation.get_action_category_display()
                ),
                "disease": recommendation.signal.primary_disease.name,
                "location": recommendation.signal.primary_location.name,
                "target_unit": recommendation.target_unit,
                "due_date": recommendation.due_date,
                "overdue": overdue,
                "action": action,
                "url": _recommendation_url(recommendation),
            }
        )

    rows.sort(
        key=lambda row: (
            0 if row["action"]["key"] == "overdue" else 1,
            0 if row["action"]["key"] == "decision" else 1,
            -URGENCY_SEVERITY[row["urgency"]],
            row["due_date"] or as_of_date + timedelta(days=3650),
            row["code"],
        )
    )
    return rows


def _warning_rows(warnings, map_dataset):
    province_by_warning = {
        item["id"]: item["province"]
        for item in map_dataset.warnings
    }
    rows = []
    for warning in warnings:
        presentation = WARNING_PRESENTATION[warning.level]
        signal = warning.signal
        rows.append(
            {
                "object": warning,
                "code": warning.code,
                "title": warning.title,
                "summary": warning.summary,
                "judgement": warning.analytical_judgement,
                "implications": warning.implications,
                "recommended_actions": warning.recommended_actions,
                "information_gaps": warning.information_gaps,
                "level": warning.level,
                "level_label": presentation["label"],
                "level_badge": presentation["badge"],
                "severity": LEVEL_SEVERITY[warning.level],
                "confidence_label": warning.get_confidence_level_display(),
                "disease": signal.primary_disease.name,
                "location": signal.primary_location.name,
                "province": province_by_warning.get(str(warning.pk), ""),
                "issued_at": warning.issued_at,
                "warning_url": (
                    f"{reverse('dashboard:early-warning')}"
                    f"?assessment={warning.assessment_id}"
                ),
                "signal_url": (
                    f"{reverse('dashboard:signal-workspace')}"
                    f"?signal={warning.signal_id}"
                ),
            }
        )
    rows.sort(
        key=lambda row: (
            -row["severity"],
            -row["issued_at"].timestamp(),
        )
    )
    return rows


def _signal_trend(*, as_of):
    local_date = timezone.localdate(as_of)
    first_day = local_date - timedelta(days=13)
    signals = (
        Signal.objects.filter(
            status__in=ANALYSABLE_SIGNAL_STATUSES,
            first_detected_at__date__gte=first_day,
            first_detected_at__date__lte=local_date,
        )
        .values_list("first_detected_at", flat=True)
    )
    daily_counts = Counter(
        timezone.localtime(detected_at).date()
        for detected_at in signals
    )
    maximum = max(daily_counts.values(), default=1)
    points = []
    for offset in range(14):
        day = first_day + timedelta(days=offset)
        count = daily_counts[day]
        points.append(
            {
                "date": day,
                "label": day.strftime("%d/%m"),
                "count": count,
                "height": max(round(count / maximum * 100), 5) if count else 3,
                "current_week": offset >= 7,
            }
        )

    previous_total = sum(point["count"] for point in points[:7])
    current_total = sum(point["count"] for point in points[7:])
    delta = current_total - previous_total
    if delta > 0:
        direction = "Naik"
        direction_class = "text-danger"
    elif delta < 0:
        direction = "Turun"
        direction_class = "text-success"
    else:
        direction = "Stabil"
        direction_class = "text-secondary"

    return {
        "points": points,
        "previous_total": previous_total,
        "current_total": current_total,
        "delta": delta,
        "direction": direction,
        "direction_class": direction_class,
    }


def _latest_update(*, warnings, recommendations, signals, fallback):
    timestamps = [fallback]
    timestamps.extend(warning.updated_at for warning in warnings)
    timestamps.extend(
        recommendation.updated_at for recommendation in recommendations
    )
    timestamps.extend(signal.last_updated_at for signal in signals)
    return max(timestamps)


def build_executive_dashboard_dataset(*, as_of=None) -> dict:
    """Build a read-only, traceable executive situational picture."""
    as_of = as_of or timezone.now()
    as_of_date = timezone.localdate(as_of)

    warnings = list(active_warning_queryset())
    map_dataset = build_threat_map_dataset(warnings)
    warning_rows = _warning_rows(warnings, map_dataset)
    top_warning = warning_rows[0] if warning_rows else None

    recommendations = list(_recommendation_queryset())
    recommendation_rows = _recommendation_rows(
        recommendations,
        as_of_date=as_of_date,
    )

    active_signals = list(
        Signal.objects.filter(status__in=ACTIVE_SIGNAL_STATUSES)
        .select_related("primary_disease", "primary_location")
        .order_by("-last_updated_at")
    )
    applied_assessment_ids = {
        str(assessment_id)
        for assessment_id in SignalHistory.objects.filter(
            metadata__action="assessment_applied",
        ).values_list("metadata__assessment_id", flat=True)
        if assessment_id
    }
    applied_assessment_ids.update(
        str(warning.assessment_id) for warning in warnings
    )
    applied_assessment_ids.update(
        str(recommendation.assessment_id)
        for recommendation in recommendations
    )
    current_completed_assessments = SignalAssessment.objects.filter(
        pk__in=applied_assessment_ids,
        is_current=True,
        status=SignalAssessment.Status.COMPLETED,
    ).count()

    diseases = build_disease_priority_dataset(as_of=as_of)
    priority_diseases = [
        disease
        for disease in diseases
        if disease["attention"]["severity"] > 1
    ][:5]

    warning_counts = Counter(warning.level for warning in warnings)
    confidence_counts = Counter(
        warning.confidence_level for warning in warnings
    )
    draft_count = sum(
        recommendation.status == IntelligenceRecommendation.Status.DRAFT
        for recommendation in recommendations
    )
    active_recommendation_count = sum(
        recommendation.status in ACTIVE_RECOMMENDATION_STATUSES
        for recommendation in recommendations
    )
    overdue_count = sum(row["overdue"] for row in recommendation_rows)
    not_started_count = sum(
        recommendation.status == IntelligenceRecommendation.Status.APPROVED
        for recommendation in recommendations
    )

    highest_level = (
        {
            "key": top_warning["level"],
            "label": top_warning["level_label"],
            "badge": top_warning["level_badge"],
            "severity": top_warning["severity"],
        }
        if top_warning
        else {
            "key": "none",
            "label": "Tidak Ada Peringatan Aktif",
            "badge": "bg-secondary-lt",
            "severity": 0,
        }
    )

    provinces = map_dataset.provinces[:5]
    max_province_severity = max(
        (province["severity"] for province in provinces),
        default=1,
    )
    for province in provinces:
        province["bar_width"] = round(
            province["severity"] / max_province_severity * 100
        )
        province["badge"] = WARNING_PRESENTATION[
            province["level"]
        ]["badge"]

    return {
        "as_of": as_of,
        "last_updated_at": _latest_update(
            warnings=warnings,
            recommendations=recommendations,
            signals=active_signals,
            fallback=as_of,
        ),
        "summary": {
            "highest_level": highest_level,
            "active_warning_count": len(warnings),
            "active_signal_count": len(active_signals),
            "current_assessment_count": current_completed_assessments,
            "active_recommendation_count": active_recommendation_count,
            "draft_recommendation_count": draft_count,
            "overdue_recommendation_count": overdue_count,
            "not_started_recommendation_count": not_started_count,
            "priority_disease_count": len(priority_diseases),
            "affected_province_count": len(map_dataset.provinces),
        },
        "warning_counts": {
            level: warning_counts[level]
            for level in EarlyWarning.Level.values
        },
        "confidence_counts": {
            level: confidence_counts[level]
            for level in SignalAssessment.RecommendedConfidence.values
        },
        "top_warning": top_warning,
        "warning_rows": warning_rows[:5],
        "recommendation_rows": recommendation_rows[:6],
        "priority_diseases": priority_diseases,
        "provinces": provinces,
        "unmapped_warning_count": len(map_dataset.unmapped_warnings),
        "trend": _signal_trend(as_of=as_of),
        "links": {
            "signals": reverse("dashboard:signal-workspace"),
            "assessments": reverse("dashboard:threat-assessment"),
            "warnings": reverse("dashboard:early-warning"),
            "map": reverse("dashboard:threat-map"),
            "diseases": reverse("dashboard:disease-priority"),
            "recommendations": reverse(
                "dashboard:intelligence-recommendation"
            ),
        },
    }
