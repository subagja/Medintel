"""Operational threat-level resolution for Dashboard Ringkasan."""

from __future__ import annotations

from dataclasses import dataclass

from apps.assessments.models import EarlyWarning, SignalAssessment
from apps.signals.models import Signal


ACTIVE_SIGNAL_STATUSES = (
    Signal.Status.VALIDATED,
    Signal.Status.CORRECTED,
    Signal.Status.ESCALATED,
)

SEVERITY = {
    SignalAssessment.RecommendedPriority.LOW: 1,
    SignalAssessment.RecommendedPriority.MEDIUM: 2,
    SignalAssessment.RecommendedPriority.HIGH: 3,
    SignalAssessment.RecommendedPriority.CRITICAL: 4,
}

PRESENTATION = {
    1: ("Rendah", "text-success"),
    2: ("Sedang", "text-warning"),
    3: ("Tinggi", "text-danger"),
    4: ("Kritis", "text-danger"),
}

WARNING_PRIORITY = {
    EarlyWarning.Level.MONITORING: SignalAssessment.RecommendedPriority.LOW,
    EarlyWarning.Level.ADVISORY: SignalAssessment.RecommendedPriority.MEDIUM,
    EarlyWarning.Level.HIGH: SignalAssessment.RecommendedPriority.HIGH,
    EarlyWarning.Level.CRITICAL: SignalAssessment.RecommendedPriority.CRITICAL,
}


@dataclass(frozen=True)
class DashboardThreatLevel:
    label: str
    css_class: str
    basis: str
    severity: int


def resolve_dashboard_threat_level() -> DashboardThreatLevel:
    """Return the most severe active analytical product.

    Both current completed assessments and current issued early warnings are
    considered. This prevents a single critical threat from being diluted by
    a count-based rule and avoids treating unassessed data as low risk.
    """

    candidates: list[tuple[int, int, str]] = []

    assessments = SignalAssessment.objects.filter(
        is_current=True,
        status=SignalAssessment.Status.COMPLETED,
        signal__status__in=ACTIVE_SIGNAL_STATUSES,
    ).values_list("recommended_priority", "signal__code")
    for priority, signal_code in assessments:
        severity = SEVERITY.get(priority)
        if severity:
            candidates.append(
                (severity, 0, f"Assessment aktif {signal_code}")
            )

    warnings = EarlyWarning.objects.filter(
        is_current=True,
        status=EarlyWarning.Status.ISSUED,
    ).values_list("level", "code")
    for level, warning_code in warnings:
        priority = WARNING_PRIORITY.get(level)
        severity = SEVERITY.get(priority)
        if severity:
            candidates.append(
                (severity, 1, f"Peringatan dini aktif {warning_code}")
            )

    if not candidates:
        return DashboardThreatLevel(
            label="Belum Dinilai",
            css_class="text-secondary",
            basis="Belum ada assessment atau peringatan dini aktif.",
            severity=0,
        )

    severity, _, basis = max(candidates, key=lambda item: (item[0], item[1]))
    label, css_class = PRESENTATION[severity]
    return DashboardThreatLevel(
        label=label,
        css_class=css_class,
        basis=basis,
        severity=severity,
    )
