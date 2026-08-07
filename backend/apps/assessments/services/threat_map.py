from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
import re

from django.urls import reverse

from apps.locations.models import Location

from ..models import EarlyWarning


LEVEL_SEVERITY = {
    EarlyWarning.Level.MONITORING: 1,
    EarlyWarning.Level.ADVISORY: 2,
    EarlyWarning.Level.HIGH: 3,
    EarlyWarning.Level.CRITICAL: 4,
}


@dataclass(frozen=True)
class ThreatMapDataset:
    warnings: list[dict]
    provinces: list[dict]
    unmapped_warnings: list[dict]


def active_warning_queryset():
    return (
        EarlyWarning.objects.filter(
            is_current=True,
            status=EarlyWarning.Status.ISSUED,
        )
        .select_related(
            "assessment",
            "signal__primary_disease",
            "signal__primary_location",
            "signal__primary_location__parent",
            "signal__primary_location__parent__parent",
            "signal__primary_location__parent__parent__parent",
        )
        .prefetch_related(
            "signal__signal_articles__article__source",
        )
        .order_by("-issued_at")
    )


def normalize_province_name(value: str) -> str:
    normalized = re.sub(r"\s+", " ", (value or "").strip().casefold())
    if normalized.startswith("provinsi "):
        normalized = normalized.removeprefix("provinsi ")
    return re.sub(r"[^a-z0-9]", "", normalized)


def resolve_province(location: Location | None) -> Location | None:
    current = location
    visited = set()

    while current and current.pk not in visited:
        visited.add(current.pk)
        if (
            current.administrative_level
            == Location.AdministrativeLevel.PROVINCE
        ):
            return current
        current = current.parent

    if not location or location.country_code != "ID":
        return None

    code = re.sub(r"\D", "", location.code or "")
    if len(code) < 2:
        return None

    return (
        Location.objects.filter(
            administrative_level=Location.AdministrativeLevel.PROVINCE,
            country_code="ID",
            code=code[:2],
            is_active=True,
        )
        .order_by("name")
        .first()
    )


def _coordinate(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _warning_item(
    warning: EarlyWarning,
    province: Location | None,
) -> dict:
    signal = warning.signal
    location = signal.primary_location
    sources = {
        link.article.source.name
        for link in signal.signal_articles.all()
        if link.article_id and link.article.source_id
    }
    return {
        "id": str(warning.pk),
        "code": warning.code,
        "title": warning.title,
        "summary": warning.summary,
        "level": warning.level,
        "level_label": warning.get_level_display(),
        "severity": LEVEL_SEVERITY[warning.level],
        "confidence": warning.confidence_level,
        "confidence_label": warning.get_confidence_level_display(),
        "disease_id": str(signal.primary_disease_id),
        "disease": signal.primary_disease.name,
        "location_id": str(signal.primary_location_id),
        "location": location.name,
        "province": province.name if province else "",
        "province_key": (
            normalize_province_name(province.name) if province else ""
        ),
        "province_code": province.code if province else "",
        "latitude": _coordinate(location.latitude),
        "longitude": _coordinate(location.longitude),
        "issued_at": warning.issued_at.isoformat(),
        "article_count": len(signal.signal_articles.all()),
        "sources": sorted(sources),
        "warning_url": (
            f"{reverse('dashboard:early-warning')}"
            f"?assessment={warning.assessment_id}"
        ),
        "signal_url": (
            f"{reverse('dashboard:signal-workspace')}"
            f"?signal={warning.signal_id}"
        ),
    }


def build_threat_map_dataset(
    warnings: Iterable[EarlyWarning],
) -> ThreatMapDataset:
    warning_items = []
    unmapped_items = []
    province_groups = {}

    for warning in warnings:
        province = resolve_province(warning.signal.primary_location)
        item = _warning_item(warning, province)
        warning_items.append(item)

        if province is None:
            unmapped_items.append(item)
            continue

        key = item["province_key"]
        group = province_groups.setdefault(
            key,
            {
                "name": province.name,
                "key": key,
                "code": province.code,
                "level": item["level"],
                "level_label": item["level_label"],
                "severity": item["severity"],
                "warning_count": 0,
                "diseases": set(),
                "warnings": [],
            },
        )
        group["warning_count"] += 1
        group["diseases"].add(item["disease"])
        group["warnings"].append(item)

        if item["severity"] > group["severity"]:
            group["level"] = item["level"]
            group["level_label"] = item["level_label"]
            group["severity"] = item["severity"]

    provinces = []
    for group in province_groups.values():
        group["diseases"] = sorted(group["diseases"])
        group["warnings"].sort(
            key=lambda item: (-item["severity"], item["code"])
        )
        provinces.append(group)

    provinces.sort(
        key=lambda item: (
            -item["severity"],
            -item["warning_count"],
            item["name"],
        )
    )

    return ThreatMapDataset(
        warnings=warning_items,
        provinces=provinces,
        unmapped_warnings=unmapped_items,
    )
