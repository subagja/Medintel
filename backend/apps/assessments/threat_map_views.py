from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.entities.models import Disease

from .models import EarlyWarning
from .services.threat_map import (
    LEVEL_SEVERITY,
    active_warning_queryset,
    build_threat_map_dataset,
)


def _valid_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        return ""


def threat_map_workspace(request: HttpRequest) -> HttpResponse:
    all_active = active_warning_queryset()
    disease_choices = Disease.objects.filter(
        primary_signals__early_warnings__is_current=True,
        primary_signals__early_warnings__status=EarlyWarning.Status.ISSUED,
    ).distinct().order_by("name")

    selected_disease = _valid_uuid(request.GET.get("disease", ""))
    selected_level = request.GET.get("level", "")
    if selected_level not in LEVEL_SEVERITY:
        selected_level = ""

    filtered = all_active
    if selected_disease:
        filtered = filtered.filter(
            signal__primary_disease_id=selected_disease
        )
    if selected_level:
        filtered = filtered.filter(level=selected_level)

    warnings = list(filtered)
    dataset = build_threat_map_dataset(warnings)

    selected_warning_id = _valid_uuid(request.GET.get("warning", ""))
    selected_warning = next(
        (
            item
            for item in dataset.warnings
            if item["id"] == selected_warning_id
        ),
        dataset.warnings[0] if dataset.warnings else None,
    )

    highest_province = dataset.provinces[0] if dataset.provinces else None
    summary = {
        "active": len(dataset.warnings),
        "provinces": len(dataset.provinces),
        "highest_level": (
            highest_province["level_label"]
            if highest_province
            else "Belum ada"
        ),
        "unmapped": len(dataset.unmapped_warnings),
    }

    return render(
        request,
        "assessments/threat_map_workspace.html",
        {
            "page_title": "Peta Ancaman",
            "active_menu": "threat_map",
            "disease_choices": disease_choices,
            "level_choices": EarlyWarning.Level.choices,
            "selected_disease": selected_disease,
            "selected_level": selected_level,
            "selected_warning": selected_warning,
            "warnings": dataset.warnings,
            "provinces": dataset.provinces,
            "unmapped_warnings": dataset.unmapped_warnings,
            "summary": summary,
        },
    )
