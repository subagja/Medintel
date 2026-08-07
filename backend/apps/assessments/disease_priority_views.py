from datetime import timedelta
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.permissions import Roles, require_role

from .services.disease_priority import (
    ATTENTION_LEVELS,
    build_disease_priority_dataset,
)


def _valid_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        return ""


@require_role(*Roles.ALL)
def disease_priority_workspace(request: HttpRequest) -> HttpResponse:
    as_of = timezone.now()
    all_items = build_disease_priority_dataset(as_of=as_of)

    selected_category = request.GET.get("category", "").strip()
    valid_categories = {
        item["category"]
        for item in all_items
        if item["category"]
    }
    if selected_category not in valid_categories:
        selected_category = ""

    selected_attention = request.GET.get("attention", "").strip()
    if selected_attention not in ATTENTION_LEVELS:
        selected_attention = ""

    query = request.GET.get("q", "").strip()[:100]
    normalized_query = query.casefold()

    items = []
    for item in all_items:
        if selected_category and item["category"] != selected_category:
            continue
        if (
            selected_attention
            and item["attention"]["key"] != selected_attention
        ):
            continue
        if normalized_query:
            searchable = " ".join(
                [
                    item["name"],
                    item["canonical_name"],
                    item["category_label"],
                    " ".join(item["programs"]),
                ]
            ).casefold()
            if normalized_query not in searchable:
                continue
        items.append(item)

    selected_disease_id = _valid_uuid(request.GET.get("disease", ""))
    selected_item = next(
        (
            item
            for item in items
            if item["id"] == selected_disease_id
        ),
        items[0] if items else None,
    )

    category_choices = sorted(
        {
            (item["category"], item["category_label"])
            for item in all_items
            if item["category"]
        },
        key=lambda choice: choice[1],
    )
    attention_choices = [
        (key, value["label"])
        for key, value in sorted(
            ATTENTION_LEVELS.items(),
            key=lambda pair: -pair[1]["severity"],
        )
    ]

    summary = {
        "monitored": len(all_items),
        "active_diseases": sum(
            item["active_signal_count"] > 0 for item in all_items
        ),
        "active_warnings": sum(
            item["active_warning_count"] for item in all_items
        ),
        "needs_assessment": sum(
            item["active_signal_count"] > 0
            and item["attention"]["key"] == "unassessed"
            for item in all_items
        ),
        "high_attention": sum(
            item["attention"]["key"] in {"high", "critical"}
            for item in all_items
        ),
    }

    return render(
        request,
        "assessments/disease_priority_workspace.html",
        {
            "page_title": "Penyakit Prioritas",
            "active_menu": "disease_priority",
            "items": items,
            "selected_item": selected_item,
            "summary": summary,
            "category_choices": category_choices,
            "attention_choices": attention_choices,
            "selected_category": selected_category,
            "selected_attention": selected_attention,
            "query": query,
            "as_of": as_of,
            "current_window_start": as_of - timedelta(days=7),
            "previous_window_start": as_of - timedelta(days=14),
        },
    )
