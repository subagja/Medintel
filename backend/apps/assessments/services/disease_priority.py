from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Iterable

from django.db.models import Prefetch, Q
from django.urls import reverse
from django.utils import timezone

from apps.entities.models import Disease, SurveillanceDisease
from apps.signals.models import Signal, SignalHistory

from ..models import EarlyWarning, SignalAssessment


ANALYSABLE_SIGNAL_STATUSES = {
    Signal.Status.VALIDATED,
    Signal.Status.CORRECTED,
    Signal.Status.ESCALATED,
    Signal.Status.CLOSED,
}

ACTIVE_SIGNAL_STATUSES = {
    Signal.Status.VALIDATED,
    Signal.Status.CORRECTED,
    Signal.Status.ESCALATED,
}

CATEGORY_LABELS = {
    "potensial_klb": "Potensial KLB",
    "menular_langsung": "Menular Langsung",
    "tular_vektor": "Tular Vektor",
    "zoonosis": "Zoonosis",
    "emerging_reemerging": "Emerging/Re-emerging",
}

ATTENTION_LEVELS = {
    "critical": {
        "label": "Kritis",
        "severity": 5,
        "badge": "bg-danger text-white",
    },
    "high": {
        "label": "Tinggi",
        "severity": 4,
        "badge": "bg-orange-lt",
    },
    "advisory": {
        "label": "Waspada",
        "severity": 3,
        "badge": "bg-yellow-lt",
    },
    "monitoring": {
        "label": "Pemantauan",
        "severity": 2,
        "badge": "bg-blue-lt",
    },
    "unassessed": {
        "label": "Belum Dinilai",
        "severity": 1,
        "badge": "bg-secondary-lt",
    },
    "none": {
        "label": "Belum Ada Sinyal",
        "severity": 0,
        "badge": "bg-secondary-lt",
    },
}

WARNING_TO_ATTENTION = {
    EarlyWarning.Level.MONITORING: "monitoring",
    EarlyWarning.Level.ADVISORY: "advisory",
    EarlyWarning.Level.HIGH: "high",
    EarlyWarning.Level.CRITICAL: "critical",
}

ASSESSMENT_TO_ATTENTION = {
    SignalAssessment.RecommendedPriority.LOW: "monitoring",
    SignalAssessment.RecommendedPriority.MEDIUM: "advisory",
    SignalAssessment.RecommendedPriority.HIGH: "high",
    SignalAssessment.RecommendedPriority.CRITICAL: "critical",
}


def priority_disease_queryset():
    """Return the monitored Disease Master without creating a second master."""
    return (
        Disease.objects.filter(is_active=True)
        .filter(
            Q(is_priority=True)
            | Q(
                surveillance_memberships__is_active=True,
                surveillance_memberships__program__status=(
                    "active"
                ),
            )
        )
        .prefetch_related(
            Prefetch(
                "surveillance_memberships",
                queryset=(
                    SurveillanceDisease.objects.filter(
                        is_active=True,
                        program__status="active",
                    )
                    .select_related("program")
                    .order_by("program__name")
                ),
                to_attr="active_surveillance_memberships",
            )
        )
        .distinct()
        .order_by("name")
    )


def _signal_queryset(disease_ids: Iterable):
    current_assessments = (
        SignalAssessment.objects.filter(
            is_current=True,
            status=SignalAssessment.Status.COMPLETED,
        )
        .order_by("-version")
    )
    active_warnings = (
        EarlyWarning.objects.filter(
            is_current=True,
            status=EarlyWarning.Status.ISSUED,
        )
        .select_related("assessment")
        .order_by("-issued_at")
    )
    histories = SignalHistory.objects.only(
        "signal_id",
        "metadata",
        "changed_at",
    ).order_by("-changed_at")

    return (
        Signal.objects.filter(
            primary_disease_id__in=disease_ids,
            status__in=ANALYSABLE_SIGNAL_STATUSES,
        )
        .select_related("primary_disease", "primary_location")
        .prefetch_related(
            Prefetch(
                "assessments",
                queryset=current_assessments,
                to_attr="current_completed_assessments",
            ),
            Prefetch(
                "early_warnings",
                queryset=active_warnings,
                to_attr="active_early_warnings",
            ),
            Prefetch(
                "histories",
                queryset=histories,
                to_attr="priority_histories",
            ),
        )
        .order_by("-last_updated_at")
    )


def _assessment_is_applied(
    assessment: SignalAssessment,
    histories: Iterable[SignalHistory],
) -> bool:
    assessment_id = str(assessment.pk)
    return any(
        isinstance(history.metadata, dict)
        and history.metadata.get("action") == "assessment_applied"
        and history.metadata.get("assessment_id") == assessment_id
        for history in histories
    )


def _attention_from_signal(signal: Signal) -> dict:
    warnings = list(signal.active_early_warnings)
    if warnings:
        warning = max(
            warnings,
            key=lambda item: ATTENTION_LEVELS[
                WARNING_TO_ATTENTION[item.level]
            ]["severity"],
        )
        key = WARNING_TO_ATTENTION[warning.level]
        return {
            "key": key,
            "basis": f"Peringatan aktif {warning.code}",
            "warning": warning,
            "assessment": warning.assessment,
            **ATTENTION_LEVELS[key],
        }

    assessments = list(signal.current_completed_assessments)
    assessment = assessments[0] if assessments else None
    if assessment and _assessment_is_applied(
        assessment,
        signal.priority_histories,
    ):
        key = ASSESSMENT_TO_ATTENTION[
            assessment.recommended_priority
        ]
        return {
            "key": key,
            "basis": f"Assessment v{assessment.version} dikonfirmasi",
            "warning": None,
            "assessment": assessment,
            **ATTENTION_LEVELS[key],
        }

    return {
        "key": "unassessed",
        "basis": (
            f"{signal.code} belum memiliki assessment terkonfirmasi"
        ),
        "warning": None,
        "assessment": assessment,
        **ATTENTION_LEVELS["unassessed"],
    }


def _trend(current_count: int, previous_count: int) -> dict:
    delta = current_count - previous_count
    if current_count > 0 and previous_count == 0:
        return {
            "key": "new",
            "label": "Sinyal Baru",
            "icon": "arrow-up-right",
            "class": "text-primary",
            "delta": delta,
        }
    if delta > 0:
        return {
            "key": "up",
            "label": "Naik",
            "icon": "arrow-up",
            "class": "text-danger",
            "delta": delta,
        }
    if delta < 0:
        return {
            "key": "down",
            "label": "Turun",
            "icon": "arrow-down",
            "class": "text-success",
            "delta": delta,
        }
    if current_count:
        return {
            "key": "steady",
            "label": "Stabil",
            "icon": "minus",
            "class": "text-secondary",
            "delta": 0,
        }
    return {
        "key": "none",
        "label": "Belum Ada Aktivitas",
        "icon": "minus",
        "class": "text-secondary",
        "delta": 0,
    }


def _category(disease: Disease) -> tuple[str, str]:
    memberships = list(disease.active_surveillance_memberships)
    key = memberships[0].category if memberships else disease.category
    return key, CATEGORY_LABELS.get(
        key,
        (key or "Belum dikategorikan").replace("_", " ").title(),
    )


def _signal_links(signal: Signal, attention: dict) -> dict:
    assessment = attention.get("assessment")
    warning = attention.get("warning")
    return {
        "signal_url": (
            f"{reverse('dashboard:signal-workspace')}?signal={signal.pk}"
        ),
        "assessment_url": (
            f"{reverse('dashboard:threat-assessment')}?signal={signal.pk}"
        ),
        "warning_url": (
            f"{reverse('dashboard:early-warning')}"
            f"?assessment={assessment.pk}"
            if warning and assessment
            else ""
        ),
        "map_url": (
            f"{reverse('dashboard:threat-map')}"
            f"?disease={signal.primary_disease_id}"
            f"&warning={warning.pk}"
            if warning
            else ""
        ),
    }


def build_disease_priority_dataset(*, as_of=None) -> list[dict]:
    """
    Build an auditable operational ranking for monitored diseases.

    Disease Master membership defines what is monitored. Operational attention
    comes only from analyst-confirmed assessment output or an active warning.
    Article volume is deliberately excluded from the level calculation.
    """
    as_of = as_of or timezone.now()
    current_start = as_of - timedelta(days=7)
    previous_start = as_of - timedelta(days=14)

    diseases = list(priority_disease_queryset())
    signals_by_disease = defaultdict(list)
    for signal in _signal_queryset([disease.pk for disease in diseases]):
        signals_by_disease[signal.primary_disease_id].append(signal)

    items = []
    for disease in diseases:
        all_signals = signals_by_disease[disease.pk]
        active_signals = [
            signal
            for signal in all_signals
            if signal.status in ACTIVE_SIGNAL_STATUSES
        ]
        current_count = sum(
            signal.first_detected_at >= current_start
            for signal in all_signals
        )
        previous_count = sum(
            previous_start <= signal.first_detected_at < current_start
            for signal in all_signals
        )
        trend_scale = max(current_count, previous_count, 1)

        signal_attention = [
            (signal, _attention_from_signal(signal))
            for signal in active_signals
        ]
        if signal_attention:
            lead_signal, attention = max(
                signal_attention,
                key=lambda pair: (
                    pair[1]["severity"],
                    pair[0].last_updated_at,
                ),
            )
        else:
            lead_signal = all_signals[0] if all_signals else None
            attention = {
                "key": "none",
                "basis": "Belum ada sinyal aktif tervalidasi",
                "warning": None,
                "assessment": None,
                **ATTENTION_LEVELS["none"],
            }

        category_key, category_label = _category(disease)
        assessment_pairs = [
            (signal, signal.current_completed_assessments[0])
            for signal in active_signals
            if signal.current_completed_assessments
        ]
        latest_assessment_pair = max(
            assessment_pairs,
            key=lambda pair: pair[1].updated_at,
            default=(None, None),
        )
        latest_assessment_signal, latest_assessment = (
            latest_assessment_pair
        )
        active_warnings = [
            warning
            for signal in active_signals
            for warning in signal.active_early_warnings
        ]
        active_warnings.sort(key=lambda warning: warning.issued_at, reverse=True)

        signal_rows = []
        for signal, row_attention in signal_attention:
            signal_rows.append(
                {
                    "id": str(signal.pk),
                    "code": signal.code,
                    "title": signal.title,
                    "location": signal.primary_location.name,
                    "status": signal.status,
                    "status_label": signal.get_status_display(),
                    "attention": row_attention,
                    "updated_at": signal.last_updated_at,
                    **_signal_links(signal, row_attention),
                }
            )
        signal_rows.sort(
            key=lambda row: (
                -row["attention"]["severity"],
                -row["updated_at"].timestamp(),
            )
        )

        memberships = list(disease.active_surveillance_memberships)
        item = {
            "id": str(disease.pk),
            "name": disease.name,
            "canonical_name": disease.canonical_name,
            "code": disease.code,
            "category": category_key,
            "category_label": category_label,
            "programs": [membership.program.name for membership in memberships],
            "attention": attention,
            "trend": _trend(current_count, previous_count),
            "current_signal_count": current_count,
            "previous_signal_count": previous_count,
            "current_signal_bar": round(
                current_count / trend_scale * 100
            ),
            "previous_signal_bar": round(
                previous_count / trend_scale * 100
            ),
            "active_signal_count": len(active_signals),
            "active_warning_count": len(active_warnings),
            "active_warnings": active_warnings,
            "lead_signal": lead_signal,
            "latest_location": (
                lead_signal.primary_location.name if lead_signal else ""
            ),
            "latest_assessment": latest_assessment,
            "latest_assessment_applied": (
                bool(
                    latest_assessment
                    and latest_assessment_signal
                    and (
                        _assessment_is_applied(
                            latest_assessment,
                            latest_assessment_signal.priority_histories,
                        )
                        or any(
                            warning.assessment_id
                            == latest_assessment.pk
                            for warning in active_warnings
                        )
                    )
                )
            ),
            "signals": signal_rows[:10],
        }
        items.append(item)

    items.sort(
        key=lambda item: (
            -item["attention"]["severity"],
            -item["active_warning_count"],
            -(
                item["latest_assessment"].priority_score
                if item["latest_assessment"]
                else 0
            ),
            -item["current_signal_count"],
            item["name"],
        )
    )
    return items
