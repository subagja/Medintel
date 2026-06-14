import os
import csv
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from intel.services.signal_assessment import build_assessment
from django.conf import settings
from django.http import HttpResponse, JsonResponse, FileResponse, Http404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User, Group
from .permissions import role_required, user_has_role, ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER
from django.core.paginator import Paginator
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count, Avg, Min, Max, Prefetch, Q
from django.db.models.functions import TruncDate
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from intel.services.signal_assessment import build_assessment
from datetime import timedelta
from collections import defaultdict
from .forms import (
    GeocodeManualUpdateForm,
    LocationForm,
    LocationAliasForm,
    ScoringRuleForm,
    SystemSettingForm,
    AlertStatusForm,
    UserRoleAssignmentForm,
)
from .models import (
    Signal,
    SignalCluster,
    SignalLocation,
    AuditLog,
    Location,
    LocationAlias,
    ScoringRule,
    SystemSetting,
    Alert,
    PublisherDomainAlias,
)


def _stable_disease_color(disease_name):
    """
    Deterministic color by disease name so the same disease keeps the same color.
    """
    palette = [
        "#2fb344",  # green
        "#7c3aed",  # purple
        "#2563eb",  # blue
        "#e11d48",  # rose
        "#f97316",  # orange
        "#0891b2",  # cyan
        "#db2777",  # pink
        "#16a34a",  # emerald
        "#ca8a04",  # amber
        "#4f46e5",  # indigo
        "#dc2626",  # red
        "#0f766e",  # teal
    ]
    if not disease_name:
        return "#6b7280"
    idx = abs(hash(str(disease_name).lower().strip())) % len(palette)
    return palette[idx]


def _date_range_for_box_map(request):
    days_raw = (request.GET.get("days") or "7").strip()
    date_to_raw = (request.GET.get("date_to") or "").strip()

    try:
        days = int(days_raw)
    except Exception:
        days = 7

    if days not in [7, 14, 30, 60, 90]:
        days = 7

    if date_to_raw:
        try:
            end_date = timezone.datetime.fromisoformat(date_to_raw).date()
        except Exception:
            end_date = timezone.now().date()
    else:
        end_date = timezone.now().date()

    start_date = end_date - timedelta(days=days - 1)
    return days, start_date, end_date


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def disease_box_map(request):
    """
    Separate documentation-style disease map.

    Main idea:
    - Left side: neutral Indonesia map.
    - Right side: inset boxes that show province mini-maps.
    - Each box represents a high-volume disease-location combination.
    - Ranking is based on total signals, high-risk count, and average score.
    """
    days, start_date, end_date = _date_range_for_box_map(request)
    selected_disease = (request.GET.get("disease") or "").strip()
    status_filter = (request.GET.get("status_filter") or "operational").strip()
    box_limit_raw = (request.GET.get("box_limit") or "10").strip()

    try:
        box_limit = int(box_limit_raw)
    except Exception:
        box_limit = 10

    if box_limit not in [6, 8, 10, 12, 16]:
        box_limit = 10

    # Reuse your existing report status logic.
    # If status_filter is sent through GET, get_report_status_config() reads it.
    _, selected_statuses, status_label = get_report_status_config(request)

    base_qs = (
        Signal.objects.filter(
            published_at__date__gte=start_date,
            published_at__date__lte=end_date,
            status__in=selected_statuses,
        )
        .exclude(status="noise")
        .select_related("source")
    )

    if selected_disease:
        base_qs = base_qs.filter(disease_tag__iexact=selected_disease)

    links = (
        SignalLocation.objects.filter(
            signal__in=base_qs,
            is_primary=True,
            location__isnull=False,
        )
        .select_related("signal", "signal__source", "location", "location__parent")
        .order_by("-signal__threat_score", "-signal__published_at", "-signal__id")
    )

    province_map = {}
    disease_location_map = defaultdict(lambda: {
        "province_code": "",
        "province_name": "",
        "disease_tag": "",
        "total": 0,
        "high_risk_total": 0,
        "score_sum": 0.0,
        "signals": [],
    })

    for link in links:
        signal = link.signal
        loc = link.location
        if not signal or not loc:
            continue

        # Promote city/regency to parent province.
        if loc.level == "province":
            province_code = loc.province_code or str(loc.id)
            province_name = loc.display_name or loc.name or "-"
        elif loc.parent:
            province_code = loc.parent.province_code or loc.province_code or str(loc.parent.id)
            province_name = loc.parent.display_name or loc.parent.name or "-"
        else:
            province_code = loc.province_code or str(loc.id)
            province_name = loc.display_name or loc.name or "-"

        disease = signal.disease_tag or "Tidak diketahui"
        score = signal.threat_score or 0
        is_high_risk = score >= 70

        if province_code not in province_map:
            province_map[province_code] = {
                "province_code": province_code,
                "province_name": province_name,
                "total": 0,
                "high_risk_total": 0,
                "score_sum": 0.0,
                "diseases": defaultdict(int),
            }

        province_map[province_code]["total"] += 1
        province_map[province_code]["score_sum"] += score
        province_map[province_code]["diseases"][disease] += 1
        if is_high_risk:
            province_map[province_code]["high_risk_total"] += 1

        key = (province_code, disease)
        row = disease_location_map[key]
        row["province_code"] = province_code
        row["province_name"] = province_name
        row["disease_tag"] = disease
        row["total"] += 1
        row["score_sum"] += score
        if is_high_risk:
            row["high_risk_total"] += 1

        if len(row["signals"]) < 4:
            row["signals"].append({
                "id": signal.id,
                "title": signal.title or "-",
                "score": score,
                "published_at": signal.published_at.strftime("%Y-%m-%d") if signal.published_at else "",
                "source": signal.source.name if signal.source else "",
                "url": signal.source_url or "",
            })

    # Province summary used by the main map and recap table.
    province_rows = []
    for item in province_map.values():
        dominant_disease = "-"
        dominant_total = 0
        if item["diseases"]:
            dominant_disease, dominant_total = sorted(
                item["diseases"].items(),
                key=lambda x: (-x[1], x[0])
            )[0]

        total = item["total"] or 0
        avg_score = round(item["score_sum"] / total, 2) if total else 0
        province_rows.append({
            "province_code": item["province_code"],
            "province_name": item["province_name"],
            "total": total,
            "high_risk_total": item["high_risk_total"],
            "avg_score": avg_score,
            "dominant_disease": dominant_disease,
            "dominant_total": dominant_total,
        })

    province_rows.sort(key=lambda x: (-x["total"], -x["avg_score"], x["province_name"]))

    # Box ranking: disease-location combinations with highest volume.
    # This matches your request: boxes represent diseases that are currently high.
    top_boxes = []
    for item in disease_location_map.values():
        total = item["total"] or 0
        avg_score = round(item["score_sum"] / total, 2) if total else 0
        disease = item["disease_tag"] or "Tidak diketahui"
        top_boxes.append({
            "province_code": item["province_code"],
            "province_name": item["province_name"],
            "disease_tag": disease,
            "total": total,
            "high_risk_total": item["high_risk_total"],
            "avg_score": avg_score,
            "color": _stable_disease_color(disease),
            "signals": item["signals"],
        })

    top_boxes.sort(
        key=lambda x: (-x["total"], -x["high_risk_total"], -x["avg_score"], x["province_name"], x["disease_tag"])
    )
    top_boxes = top_boxes[:box_limit]

    # Disease legend from boxes only, grouped by disease.
    disease_legend_map = {}
    for box in top_boxes:
        disease = box["disease_tag"] or "Tidak diketahui"
        if disease not in disease_legend_map:
            disease_legend_map[disease] = {
                "disease_tag": disease,
                "color": box["color"],
                "total": 0,
                "locations": 0,
            }
        disease_legend_map[disease]["total"] += box["total"]
        disease_legend_map[disease]["locations"] += 1

    disease_legend = sorted(
        disease_legend_map.values(),
        key=lambda x: (-x["total"], x["disease_tag"])
    )

    disease_choices = (
        Signal.objects.exclude(status="noise")
        .exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .values_list("disease_tag", flat=True)
        .distinct()
        .order_by("disease_tag")
    )

    total_signals = base_qs.count()
    high_risk_count = base_qs.filter(threat_score__gte=70).count()
    avg_score = round(base_qs.aggregate(avg=Avg("threat_score"))["avg"] or 0, 2)

    context = {
        "page_title": "Disease Box Map",
        "days": str(days),
        "box_limit": str(box_limit),
        "selected_disease": selected_disease,
        "status_filter": status_filter,
        "status_label": status_label,
        "date_to": end_date.isoformat(),
        "date_from": start_date.isoformat(),
        "disease_choices": disease_choices,
        "province_rows": province_rows,
        "top_boxes": top_boxes,
        "disease_legend": disease_legend,
        "province_rows_json": json.dumps(province_rows, default=str),
        "top_boxes_json": json.dumps(top_boxes, default=str),
        "disease_legend_json": json.dumps(disease_legend, default=str),
        "total_signals": total_signals,
        "high_risk_count": high_risk_count,
        "avg_score": avg_score,
        "mapped_provinces": len(province_rows),
    }
    return render(request, "intel/disease_box_map.html", context)

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def geojson_file(request, geo_type):
    """
    Menyajikan file GeoJSON dari folder backend/data/geo/
    agar file batas wilayah tidak perlu dipindahkan ke static.
    """

    allowed_files = {
        "indonesia-provinces": "indonesia_provinces.geojson",
        "indonesia-kabkota": "indonesia_kabkota.geojson",
    }

    filename = allowed_files.get(geo_type)
    if not filename:
        raise Http404("GeoJSON tidak dikenali.")

    base_dir = Path(settings.BASE_DIR)
    geojson_path = base_dir / "data" / "geo" / filename

    if not geojson_path.exists():
        raise Http404(f"File GeoJSON tidak ditemukan: {filename}")

    return FileResponse(
        open(geojson_path, "rb"),
        content_type="application/geo+json"
    )

def kabkota_map_view(request, province_id):
    province = get_object_or_404(
        Location,
        id=province_id,
        level="province",
        is_active=True,
        is_false_positive=False,
    )

    disease = (request.GET.get("disease") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()

    qs = SignalLocation.objects.filter(
        is_primary=True,
        location__isnull=False,
        location__level__in=["city", "regency"],
        location__parent=province,
        signal__status__in=["raw", "validated", "approved"],
    )

    if disease:
        qs = qs.filter(signal__disease_tag__iexact=disease)

    if date_from:
        qs = qs.filter(signal__published_at__date__gte=date_from)

    if date_to:
        qs = qs.filter(signal__published_at__date__lte=date_to)

    rows = list(
        qs.values(
            "location_id",
            "location__display_name",
            "location__name",
            "location__level",
            "location__city_regency_code",
        )
        .annotate(
            total_signals=Count("signal_id", distinct=True),
            avg_score=Avg("signal__threat_score"),
            high_risk_count=Count(
                "signal_id",
                filter=Q(signal__is_high_risk=True),
                distinct=True,
            ),
        )
        .order_by("-total_signals", "location__display_name")
    )

    normalized_rows = []
    for row in rows:
        normalized_rows.append({
            "location_id": row["location_id"],
            "kabkota_name": row["location__display_name"] or row["location__name"],
            "level": row["location__level"],
            "city_regency_code": row["location__city_regency_code"] or "",
            "total_signals": row["total_signals"],
            "avg_score": round(row["avg_score"] or 0, 2),
            "high_risk_count": row["high_risk_count"],
        })

    disease_choices = (
        SignalLocation.objects.exclude(signal__disease_tag="")
        .exclude(signal__disease_tag__isnull=True)
        .values_list("signal__disease_tag", flat=True)
        .distinct()
        .order_by("signal__disease_tag")
    )

    return render(request, "intel/kabkota_map.html", {
        "province": province,
        "rows": normalized_rows,
        "disease": disease,
        "date_from": date_from,
        "date_to": date_to,
        "disease_choices": disease_choices,
    })

def province_map_view(request):
    disease = (request.GET.get("disease") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()

    qs = SignalLocation.objects.filter(
        is_primary=True,
        location__isnull=False,
        signal__status__in=["raw", "validated", "approved"],
    )

    if disease:
        qs = qs.filter(signal__disease_tag__iexact=disease)

    if date_from:
        qs = qs.filter(signal__published_at__date__gte=date_from)

    if date_to:
        qs = qs.filter(signal__published_at__date__lte=date_to)

    merged = {}

    # match langsung ke province
    province_direct = (
        qs.filter(location__level="province")
        .values(
            "location_id",
            "location__display_name",
            "location__name",
        )
        .annotate(
            total_signals=Count("signal_id", distinct=True),
            avg_score=Avg("signal__threat_score"),
            high_risk_count=Count(
                "signal_id",
                filter=Q(signal__is_high_risk=True),
                distinct=True,
            ),
        )
    )

    # match ke city/regency, lalu naikkan ke parent province
    province_from_children = (
        qs.filter(location__level__in=["city", "regency"], location__parent__isnull=False)
        .values(
            "location__parent_id",
            "location__parent__display_name",
            "location__parent__name",
        )
        .annotate(
            total_signals=Count("signal_id", distinct=True),
            avg_score=Avg("signal__threat_score"),
            high_risk_count=Count(
                "signal_id",
                filter=Q(signal__is_high_risk=True),
                distinct=True,
            ),
        )
    )

    for row in province_direct:
        province_id = row["location_id"]
        province_name = row["location__display_name"] or row["location__name"]

        merged.setdefault(province_id, {
            "province_id": province_id,
            "province_name": province_name,
            "total_signals": 0,
            "score_sum": 0.0,
            "score_count": 0,
            "high_risk_count": 0,
        })

        merged[province_id]["total_signals"] += row["total_signals"]
        merged[province_id]["high_risk_count"] += row["high_risk_count"]
        if row["avg_score"] is not None:
            merged[province_id]["score_sum"] += row["avg_score"] * row["total_signals"]
            merged[province_id]["score_count"] += row["total_signals"]

    for row in province_from_children:
        province_id = row["location__parent_id"]
        province_name = row["location__parent__display_name"] or row["location__parent__name"]

        merged.setdefault(province_id, {
            "province_id": province_id,
            "province_name": province_name,
            "total_signals": 0,
            "score_sum": 0.0,
            "score_count": 0,
            "high_risk_count": 0,
        })

        merged[province_id]["total_signals"] += row["total_signals"]
        merged[province_id]["high_risk_count"] += row["high_risk_count"]
        if row["avg_score"] is not None:
            merged[province_id]["score_sum"] += row["avg_score"] * row["total_signals"]
            merged[province_id]["score_count"] += row["total_signals"]

    province_rows = []
    for item in merged.values():
        avg_score = item["score_sum"] / item["score_count"] if item["score_count"] else 0
        province_rows.append({
            "province_id": item["province_id"],
            "province_name": item["province_name"],
            "total_signals": item["total_signals"],
            "avg_score": round(avg_score, 2),
            "high_risk_count": item["high_risk_count"],
        })

    province_rows.sort(key=lambda x: (-x["total_signals"], x["province_name"]))

    return render(request, "intel/province_map.html", {
        "province_rows": province_rows,
        "disease": disease,
        "date_from": date_from,
        "date_to": date_to,
    })

def province_map_data(request):
    disease = (request.GET.get("disease") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()

    qs = SignalLocation.objects.filter(
        is_primary=True,
        location__isnull=False,
        signal__status__in=["raw", "validated", "approved"],
    )

    if disease:
        qs = qs.filter(signal__disease_tag__iexact=disease)

    if date_from:
        qs = qs.filter(signal__published_at__date__gte=date_from)

    if date_to:
        qs = qs.filter(signal__published_at__date__lte=date_to)

    province_direct = (
        qs.filter(location__level="province")
        .values("location__display_name", "location__name")
        .annotate(
            total_signals=Count("signal_id", distinct=True),
            avg_score=Avg("signal__threat_score"),
            high_risk_count=Count("signal_id", filter=Q(signal__is_high_risk=True), distinct=True),
        )
    )

    province_from_children = (
        qs.filter(location__level__in=["city", "regency"], location__parent__isnull=False)
        .values("location__parent__display_name", "location__parent__name")
        .annotate(
            total_signals=Count("signal_id", distinct=True),
            avg_score=Avg("signal__threat_score"),
            high_risk_count=Count("signal_id", filter=Q(signal__is_high_risk=True), distinct=True),
        )
    )

    merged = {}

    for row in province_direct:
        name = row["location__display_name"] or row["location__name"]
        merged.setdefault(name, {
            "province_name": name,
            "total_signals": 0,
            "score_sum": 0.0,
            "score_count": 0,
            "high_risk_count": 0,
        })
        merged[name]["total_signals"] += row["total_signals"]
        merged[name]["high_risk_count"] += row["high_risk_count"]
        if row["avg_score"] is not None:
            merged[name]["score_sum"] += row["avg_score"] * row["total_signals"]
            merged[name]["score_count"] += row["total_signals"]

    for row in province_from_children:
        name = row["location__parent__display_name"] or row["location__parent__name"]
        merged.setdefault(name, {
            "province_name": name,
            "total_signals": 0,
            "score_sum": 0.0,
            "score_count": 0,
            "high_risk_count": 0,
        })
        merged[name]["total_signals"] += row["total_signals"]
        merged[name]["high_risk_count"] += row["high_risk_count"]
        if row["avg_score"] is not None:
            merged[name]["score_sum"] += row["avg_score"] * row["total_signals"]
            merged[name]["score_count"] += row["total_signals"]

    rows = []
    for item in merged.values():
        rows.append({
            "province_name": item["province_name"],
            "total_signals": item["total_signals"],
            "avg_score": round(item["score_sum"] / item["score_count"], 2) if item["score_count"] else 0,
            "high_risk_count": item["high_risk_count"],
        })

    rows.sort(key=lambda x: (-x["total_signals"], x["province_name"]))
    return JsonResponse({"rows": rows})

@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def kabkota_by_province_api(request):
    province_code = request.GET.get("province_code", "").strip()

    qs = Location.objects.filter(
        level__in=["city", "regency"],
        is_active=True,
        is_false_positive=False,
    ).order_by("display_name", "name")

    if province_code:
        qs = qs.filter(province_code=province_code)

    results = [
        {
            "id": loc.id,
            "name": loc.display_name or loc.name,
            "city_regency_code": loc.city_regency_code,
            "level": loc.level,
            "province_code": loc.province_code,
        }
        for loc in qs
    ]

    return JsonResponse({"results": results})
    
@role_required(ROLE_ADMIN)
def user_role_management(request):
    users = User.objects.all().prefetch_related("groups").order_by("username")
    groups = Group.objects.all().order_by("name")

    return render(request, "intel/user_role_management.html", {
        "page_title": "User & Role Management",
        "users": users,
        "groups": groups,
    })

@role_required(ROLE_ADMIN)
def user_role_update(request):
    if request.method == "POST":
        form = UserRoleAssignmentForm(request.POST)
        if form.is_valid():
            user = form.cleaned_data["user"]
            groups = form.cleaned_data["groups"]

            before_groups = list(user.groups.values_list("name", flat=True))
            user.groups.set(groups)
            after_groups = list(user.groups.values_list("name", flat=True))

            AuditLog.objects.create(
                user=request.user,
                action="manual_edit",
                model_name="UserRole",
                object_id=str(user.id),
                notes=f"Role assignment updated for user {user.username}",
                before_data={"groups": before_groups},
                after_data={"groups": after_groups},
            )

            messages.success(request, f"Role untuk user {user.username} berhasil diperbarui.")
            return redirect("intel:user_role_management")
    else:
        form = UserRoleAssignmentForm()

    return render(request, "intel/user_role_form.html", {
        "page_title": "Update User Role",
        "form": form,
    })

def get_system_setting_value(key, default_value):
    setting = SystemSetting.objects.filter(key=key, is_active=True).first()
    if setting and setting.value not in [None, ""]:
        return setting.value
    return default_value

def get_date_range_from_request(request):
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    # Reports should default to weekly so the bulletin does not look empty
    # when users open a submenu without selecting dates. Other pages keep daily behavior.
    default_report_type = "weekly" if "/reports/" in getattr(request, "path", "") else "daily"
    report_type = request.GET.get("report_type", default_report_type).strip()

    now = timezone.now()

    if not date_to:
        date_to_obj = now.date()
    else:
        date_to_obj = timezone.datetime.fromisoformat(date_to).date()

    if not date_from:
        if report_type == "monthly":
            date_from_obj = date_to_obj - timedelta(days=29)
        elif report_type == "weekly":
            date_from_obj = date_to_obj - timedelta(days=6)
        else:
            date_from_obj = date_to_obj
    else:
        date_from_obj = timezone.datetime.fromisoformat(date_from).date()

    return report_type, date_from_obj, date_to_obj


def get_report_status_config(request):
    status_filter = request.GET.get("status_filter", "operational").strip()

    if status_filter == "approved":
        return status_filter, ["approved"], "Approved Mapping"
    if status_filter == "validated":
        return status_filter, ["validated", "approved"], "Validated + Approved"
    if status_filter == "raw":
        return status_filter, ["raw"], "Raw Crawling"

    return "operational", ["raw", "validated", "approved"], "Data Operasional Crawling"


def normalize_report_scope(report_scope, selected_disease, province_id, city_id):
    report_scope = (report_scope or "general").strip()
    if report_scope not in ["general", "disease", "region", "disease_region"]:
        report_scope = "general"
    return report_scope


def get_location_filter_label(province_id, city_id):
    if city_id:
        loc = Location.objects.select_related("parent").filter(id=city_id).first()
        if not loc:
            return "Wilayah terpilih"
        loc_name = loc.display_name or loc.name
        parent_name = loc.parent.display_name or loc.parent.name if loc.parent else ""
        return f"{loc_name}, {parent_name}" if parent_name else loc_name

    if province_id:
        loc = Location.objects.filter(id=province_id).first()
        return loc.display_name or loc.name if loc else "Wilayah terpilih"

    return "Seluruh Wilayah"


def apply_report_dynamic_filters(base_qs, selected_disease, province_id, city_id):
    if selected_disease:
        base_qs = base_qs.filter(disease_tag__iexact=selected_disease)

    if city_id:
        base_qs = base_qs.filter(
            locations__is_primary=True,
            locations__location_id=city_id,
        ).distinct()

    elif province_id:
        base_qs = base_qs.filter(
            Q(locations__is_primary=True, locations__location_id=province_id, locations__location__level="province")
            | Q(locations__is_primary=True, locations__location__parent_id=province_id)
        ).distinct()

    return base_qs


def classify_report_risk(avg_score, high_risk_total=0, total=0):
    avg_score = avg_score or 0
    high_risk_total = high_risk_total or 0
    total = total or 0

    if high_risk_total >= 3 or avg_score >= 70:
        return "Tinggi"
    if high_risk_total >= 1 or avg_score >= 50:
        return "Sedang-Tinggi"
    if avg_score >= 35 or total >= 3:
        return "Sedang"
    return "Rendah"


def build_location_recap(base_qs, limit=25):
    links = (
        SignalLocation.objects.filter(
            signal__in=base_qs,
            is_primary=True,
            location__isnull=False,
        )
        .select_related("signal", "location", "location__parent")
    )

    province_map = {}
    location_map = {}

    for link in links:
        signal = link.signal
        loc = link.location
        if not loc:
            continue

        if loc.level == "province":
            province_name = loc.display_name or loc.name or "-"
            province_code = loc.province_code or "-"
        elif loc.parent:
            province_name = loc.parent.display_name or loc.parent.name or "-"
            province_code = loc.parent.province_code or loc.province_code or "-"
        else:
            province_name = loc.display_name or loc.name or "-"
            province_code = loc.province_code or "-"

        province_map.setdefault(province_name, {
            "province_name": province_name,
            "province_code": province_code,
            "total": 0,
            "score_sum": 0,
            "high_risk_total": 0,
        })
        province_map[province_name]["total"] += 1
        province_map[province_name]["score_sum"] += signal.threat_score or 0
        if (signal.threat_score or 0) >= 70:
            province_map[province_name]["high_risk_total"] += 1

        location_name = loc.display_name or loc.name or "-"
        key = f"{location_name}::{loc.level}::{province_name}"
        location_map.setdefault(key, {
            "location_name": location_name,
            "province_name": province_name,
            "level": loc.level or "-",
            "total": 0,
            "score_sum": 0,
            "high_risk_total": 0,
        })
        location_map[key]["total"] += 1
        location_map[key]["score_sum"] += signal.threat_score or 0
        if (signal.threat_score or 0) >= 70:
            location_map[key]["high_risk_total"] += 1

    province_recap = []
    for item in province_map.values():
        item["avg_score"] = round(item["score_sum"] / item["total"], 2) if item["total"] else 0
        province_recap.append(item)
    province_recap.sort(key=lambda x: (-x["total"], -x["avg_score"], x["province_name"]))

    location_recap = []
    for item in location_map.values():
        item["avg_score"] = round(item["score_sum"] / item["total"], 2) if item["total"] else 0
        location_recap.append(item)
    location_recap.sort(key=lambda x: (-x["total"], -x["avg_score"], x["location_name"]))

    return province_recap, location_recap[:limit]


def build_focus_period(disease_recap, location_recap, high_risk_count, assessment_pending_count):
    focus_items = []

    for item in disease_recap[:5]:
        disease = item.get("disease_tag") or "-"
        total = item.get("total") or 0
        avg_score = round(item.get("avg_score") or 0, 2)
        high_risk_total = item.get("high_risk_total") or 0
        risk_level = classify_report_risk(avg_score, high_risk_total, total)

        if total <= 0:
            continue

        if risk_level in ["Tinggi", "Sedang-Tinggi"]:
            note = f"{disease} menjadi fokus karena memiliki {total} signal, {high_risk_total} signal high-risk, dan rata-rata skor {avg_score}."
        else:
            note = f"{disease} tetap perlu dipantau karena muncul dalam {total} signal pada periode laporan."

        focus_items.append({"title": disease, "risk_level": risk_level, "note": note})

    if location_recap:
        top_loc = location_recap[0]
        if top_loc.get("total", 0) >= 2:
            focus_items.append({
                "title": f"Konsentrasi lokasi: {top_loc.get('location_name', '-')}",
                "risk_level": classify_report_risk(top_loc.get("avg_score", 0), top_loc.get("high_risk_total", 0), top_loc.get("total", 0)),
                "note": f"Lokasi {top_loc.get('location_name', '-')} mencatat {top_loc.get('total', 0)} signal dengan rata-rata skor {top_loc.get('avg_score', 0)}.",
            })

    if high_risk_count > 0:
        focus_items.append({
            "title": "Signal risiko tinggi",
            "risk_level": "Tinggi",
            "note": f"Terdapat {high_risk_count} signal high-risk yang perlu diprioritaskan untuk verifikasi, assessment, dan pemantauan lanjutan.",
        })

    if assessment_pending_count > 0:
        focus_items.append({
            "title": "Kelengkapan assessment",
            "risk_level": "Pendukung",
            "note": f"Terdapat {assessment_pending_count} signal yang belum memiliki assessment lengkap. Signal prioritas perlu dilengkapi 5W+1H agar dapat digunakan sebagai bahan analisis.",
        })

    return focus_items[:8]


def build_disease_analysis(disease_recap, base_qs):
    rows = []

    for item in disease_recap[:6]:
        disease = item.get("disease_tag") or "-"
        total = item.get("total") or 0
        avg_score = round(item.get("avg_score") or 0, 2)
        high_risk_total = item.get("high_risk_total") or 0
        risk_level = classify_report_risk(avg_score, high_risk_total, total)

        disease_qs = base_qs.filter(disease_tag__iexact=disease)

        location_rows = (
            SignalLocation.objects.filter(signal__in=disease_qs, is_primary=True, location__isnull=False)
            .values(
                "location__display_name",
                "location__name",
                "location__parent__display_name",
                "location__parent__name",
            )
            .annotate(total=Count("signal_id", distinct=True))
            .order_by("-total", "location__display_name")[:5]
        )

        locations = []
        for loc in location_rows:
            loc_name = loc["location__display_name"] or loc["location__name"] or "-"
            parent = loc["location__parent__display_name"] or loc["location__parent__name"] or ""
            locations.append(f"{loc_name}, {parent}" if parent and parent != loc_name else loc_name)

        if not locations:
            locations = ["Belum terpetakan"]

        judgement = (
            f"{disease} terpantau dalam {total} signal OSINT pada periode laporan, "
            f"dengan rata-rata skor {avg_score} dan {high_risk_total} signal high-risk. "
            f"Tingkat perhatian diklasifikasikan {risk_level.lower()}."
        )

        if risk_level in ["Tinggi", "Sedang-Tinggi"]:
            impact = "Kondisi ini dapat mengindikasikan peningkatan atensi publik atau potensi peningkatan kejadian di lapangan, sehingga memerlukan verifikasi cepat dan pemantauan lintas sumber."
            action = "Prioritaskan assessment 5W+1H, validasi lokasi, pengecekan sumber resmi, serta koordinasi dengan pemangku kepentingan kesehatan di wilayah terkait."
        elif risk_level == "Sedang":
            impact = "Signal masih perlu dipantau karena dapat berkembang apabila muncul laporan tambahan dari wilayah atau sumber lain."
            action = "Lakukan monitoring berkala, validasi signal baru, dan bandingkan dengan data resmi apabila tersedia."
        else:
            impact = "Belum terdapat indikasi menonjol, namun signal tetap relevan sebagai bagian dari pemantauan kewaspadaan dini."
            action = "Lanjutkan pemantauan rutin dan lakukan validasi apabila terdapat peningkatan jumlah signal atau skor risiko."

        rows.append({
            "disease": disease,
            "total": total,
            "avg_score": avg_score,
            "high_risk_total": high_risk_total,
            "risk_level": risk_level,
            "locations": locations,
            "judgement": judgement,
            "impact": impact,
            "action": action,
        })

    return rows


def build_priority_signals(base_qs):
    return (
        base_qs.filter(threat_score__gte=50)
        .select_related("source")
        .order_by("-threat_score", "-published_at", "-created_at")[:15]
    )


def build_report_charts(base_qs, disease_recap, location_recap, report_scope):
    trend_rows = list(
        base_qs.exclude(published_at__isnull=True)
        .annotate(day=TruncDate("published_at"))
        .values("day")
        .annotate(
            total=Count("id", distinct=True),
            avg_score=Avg("threat_score"),
            high_risk_total=Count("id", filter=Q(threat_score__gte=70), distinct=True),
        )
        .order_by("day")
    )

    trend = {
        "labels": [row["day"].strftime("%d/%m") for row in trend_rows if row["day"]],
        "total": [row["total"] for row in trend_rows],
        "avg_score": [round(row["avg_score"] or 0, 2) for row in trend_rows],
        "high_risk": [row["high_risk_total"] for row in trend_rows],
    }

    disease_chart = {
        "labels": [item.get("disease_tag") or "-" for item in disease_recap[:10]],
        "total": [item.get("total") or 0 for item in disease_recap[:10]],
        "avg_score": [round(item.get("avg_score") or 0, 2) for item in disease_recap[:10]],
        "high_risk": [item.get("high_risk_total") or 0 for item in disease_recap[:10]],
    }

    location_chart = {
        "labels": [item.get("location_name") or "-" for item in location_recap[:10]],
        "total": [item.get("total") or 0 for item in location_recap[:10]],
        "avg_score": [round(item.get("avg_score") or 0, 2) for item in location_recap[:10]],
        "high_risk": [item.get("high_risk_total") or 0 for item in location_recap[:10]],
    }

    return {"trend": trend, "disease": disease_chart, "location": location_chart, "report_scope": report_scope}


def build_report_map_data(base_qs):
    links = (
        SignalLocation.objects.filter(
            signal__in=base_qs,
            is_primary=True,
            location__isnull=False,
            location__lat__isnull=False,
            location__lon__isnull=False,
        )
        .select_related("signal", "signal__source", "location", "location__parent")
        .order_by("-signal__threat_score", "-signal__published_at")
    )

    grouped = {}

    for link in links[:3000]:
        signal = link.signal
        loc = link.location
        if not loc or loc.lat is None or loc.lon is None:
            continue

        key = str(loc.id)
        if loc.level == "province":
            province_name = loc.display_name or loc.name or "-"
        elif loc.parent:
            province_name = loc.parent.display_name or loc.parent.name or "-"
        else:
            province_name = loc.province_code or "-"

        grouped.setdefault(key, {
            "location_id": loc.id,
            "location_name": loc.display_name or loc.name or "-",
            "province_name": province_name,
            "level": loc.level or "-",
            "lat": loc.lat,
            "lon": loc.lon,
            "total": 0,
            "high_risk_total": 0,
            "score_sum": 0,
            "diseases": {},
            "signals": [],
        })

        item = grouped[key]
        item["total"] += 1
        item["score_sum"] += signal.threat_score or 0
        if (signal.threat_score or 0) >= 70:
            item["high_risk_total"] += 1

        disease = signal.disease_tag or "-"
        item["diseases"][disease] = item["diseases"].get(disease, 0) + 1

        if len(item["signals"]) < 3:
            item["signals"].append({
                "id": signal.id,
                "title": signal.title or "-",
                "disease": signal.disease_tag or "-",
                "score": signal.threat_score or 0,
                "status": signal.status or "-",
                "url": signal.source_url or "",
                "published_at": signal.published_at.strftime("%Y-%m-%d %H:%M") if signal.published_at else "",
            })

    results = []
    for item in grouped.values():
        avg_score = round(item["score_sum"] / item["total"], 2) if item["total"] else 0
        dominant_disease = "-"
        if item["diseases"]:
            dominant_disease = sorted(item["diseases"].items(), key=lambda x: (-x[1], x[0]))[0][0]

        results.append({
            "location_id": item["location_id"],
            "location_name": item["location_name"],
            "province_name": item["province_name"],
            "level": item["level"],
            "lat": item["lat"],
            "lon": item["lon"],
            "total": item["total"],
            "high_risk_total": item["high_risk_total"],
            "avg_score": avg_score,
            "dominant_disease": dominant_disease,
            "signals": item["signals"],
        })

    results.sort(key=lambda x: (-x["total"], -x["avg_score"], x["location_name"]))
    return {"count": len(results), "points": results[:500]}


def build_report_title(report_scope, selected_disease, region_label):
    if report_scope == "disease_region":
        if selected_disease and region_label and region_label != "Seluruh Wilayah":
            return f"Laporan {selected_disease} di {region_label}"
        return "Targeted Action Brief"
    if report_scope == "disease":
        return f"Profil Penyakit {selected_disease}" if selected_disease else "Disease Intelligence Profile"
    if report_scope == "region":
        return f"Profil Wilayah {region_label}" if region_label and region_label != "Seluruh Wilayah" else "Regional Intelligence Profile"
    return "Buletin Intelijen Medik OSINT"


def get_report_readiness(report_scope, selected_disease, province_id, city_id):
    missing = []
    if report_scope == "disease" and not selected_disease:
        missing.append("penyakit")
    elif report_scope == "region" and not (province_id or city_id):
        missing.append("wilayah")
    elif report_scope == "disease_region":
        if not selected_disease:
            missing.append("penyakit")
        if not (province_id or city_id):
            missing.append("wilayah")
    return len(missing) == 0, missing


def build_scope_landing_data(seed_qs):
    disease_rows = list(
        seed_qs.exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .values("disease_tag")
        .annotate(
            total=Count("id", distinct=True),
            avg_score=Avg("threat_score"),
            high_risk_total=Count("id", filter=Q(threat_score__gte=70), distinct=True),
        )
        .order_by("-total", "-avg_score", "disease_tag")[:12]
    )

    province_rows, location_rows = build_location_recap(seed_qs, limit=12)
    return disease_rows, province_rows[:12], location_rows[:12]


def build_dynamic_executive_summary(report_scope, report_title, date_from_obj, date_to_obj, total_signals, high_risk_count, avg_score, top_disease, top_location, top_province, geocode_success_rate, assessment_rate, status_label):
    periode = f"{date_from_obj.strftime('%d/%m/%Y')} sampai {date_to_obj.strftime('%d/%m/%Y')}"

    if total_signals <= 0:
        return f"Belum terdapat signal pada periode {periode} untuk parameter laporan yang dipilih. Coba perluas rentang tanggal, pilih Data Operasional Crawling, atau ubah filter penyakit/wilayah."

    if report_scope == "disease_region":
        return f"Pada periode {periode}, {report_title} mencatat {total_signals} signal OSINT. Sebanyak {high_risk_count} signal masuk kategori high-risk dengan rata-rata skor {avg_score}. Konsentrasi lokasi utama berada pada {top_location}. Data ini bersifat indikatif dan perlu diverifikasi melalui sumber resmi atau pemangku kepentingan kesehatan setempat."

    if report_scope == "disease":
        return f"Pada periode {periode}, {report_title} mencatat {total_signals} signal OSINT yang tersebar terutama pada {top_location} / {top_province}. Sebanyak {high_risk_count} signal masuk kategori high-risk dengan rata-rata skor {avg_score}. Kondisi ini menjadi bahan kewaspadaan dini terhadap penyakit terkait."

    if report_scope == "region":
        return f"Pada periode {periode}, {report_title} mencatat {total_signals} signal OSINT penyakit menular. Penyakit dominan adalah {top_disease}, dengan konsentrasi signal pada {top_location}. Sebanyak {high_risk_count} signal masuk kategori high-risk dan perlu diprioritaskan untuk verifikasi."

    return f"Pada periode {periode}, sistem memperoleh {total_signals} signal penyakit menular dari sumber terbuka pada kategori {status_label}. Penyakit yang paling dominan terpantau adalah {top_disease}, dengan konsentrasi lokasi utama pada {top_location} / {top_province}. Dari keseluruhan signal, {high_risk_count} signal masuk kategori risiko tinggi dengan rata-rata skor {avg_score}. Tingkat keberhasilan geocoding tercatat {geocode_success_rate}%, sedangkan kelengkapan assessment berada pada {assessment_rate}%."


def build_policy_recommendations(report_scope, disease_recap, location_recap, high_risk_count, assessment_pending_count, selected_disease, region_label):
    recommendations = []
    scope_prefix = ""
    if report_scope == "disease_region":
        scope_prefix = f"untuk {selected_disease} di {region_label}"
    elif report_scope == "disease":
        scope_prefix = f"untuk penyakit {selected_disease}"
    elif report_scope == "region":
        scope_prefix = f"di wilayah {region_label}"

    if high_risk_count > 0:
        recommendations.append({
            "priority": "Tinggi",
            "basis": "Signal risiko tinggi",
            "judgement": f"Terdapat {high_risk_count} signal high-risk {scope_prefix}.".strip(),
            "impact": "Kondisi ini berpotensi menunjukkan peningkatan ancaman penyakit menular atau peningkatan atensi publik yang memerlukan verifikasi cepat.",
            "action": "Lakukan verifikasi lapangan, prioritaskan assessment 5W+1H pada signal dengan skor tertinggi, dan bandingkan dengan data resmi dari dinas kesehatan atau kanal surveilans yang tersedia.",
        })

    if location_recap:
        top_loc = location_recap[0]
        if top_loc.get("total", 0) >= 2:
            recommendations.append({
                "priority": "Sedang-Tinggi",
                "basis": "Konsentrasi lokasi",
                "judgement": f"Terdapat konsentrasi signal pada {top_loc.get('location_name', '-')} dengan total {top_loc.get('total', 0)} signal dan rata-rata skor {top_loc.get('avg_score', 0)}.",
                "impact": "Konsentrasi signal pada lokasi yang sama dapat menjadi indikator awal perlunya surveilans aktif dan validasi situasi di tingkat wilayah.",
                "action": "Dorong koordinasi dengan pemerintah daerah/dinas kesehatan setempat, lakukan pengecekan perkembangan kasus, dan pantau ulang tren dalam 3–7 hari.",
            })

    if disease_recap and report_scope in ["general", "region"]:
        top_disease = disease_recap[0]
        if top_disease.get("total", 0) >= 2:
            recommendations.append({
                "priority": "Sedang",
                "basis": "Dominasi penyakit",
                "judgement": f"{top_disease.get('disease_tag', '-')} menjadi penyakit dominan dengan {top_disease.get('total', 0)} signal.",
                "impact": "Dominasi penyakit tertentu dapat menjadi indikator awal perlunya penguatan kewaspadaan dini dan monitoring tematik.",
                "action": "Susun pemantauan tematik terhadap penyakit tersebut, cek sebaran wilayahnya, dan lakukan perbandingan dengan data resmi apabila tersedia.",
            })

    if assessment_pending_count > 0:
        recommendations.append({
            "priority": "Pendukung",
            "basis": "Kelengkapan assessment",
            "judgement": f"Masih terdapat {assessment_pending_count} signal yang belum memiliki assessment lengkap.",
            "impact": "Signal yang belum memiliki assessment dapat menurunkan kualitas analisis dan ketepatan rekomendasi kebijakan.",
            "action": "Lakukan assessment 5W+1H atau re-assessment terhadap signal prioritas, terutama yang memiliki skor tinggi dan lokasi yang sudah jelas.",
        })

    if not recommendations:
        recommendations.append({
            "priority": "Normal",
            "basis": "Pemantauan rutin",
            "judgement": "Belum terdapat indikasi menonjol pada parameter laporan yang dipilih.",
            "impact": "Situasi masih dapat dipantau melalui mekanisme monitoring rutin.",
            "action": "Lanjutkan crawling berkala, validasi signal baru, dan pemutakhiran data lokasi.",
        })

    return recommendations


def build_report_conclusions(total_signals, high_risk_count, top_disease, top_location, geocode_success_rate, assessment_rate, report_scope, report_title):
    if total_signals <= 0:
        return ["Belum terdapat signal pada periode dan kategori data yang dipilih."]

    conclusions = [f"{report_title} menghimpun {total_signals} signal OSINT penyakit menular pada periode laporan."]
    if top_disease and top_disease != "-":
        conclusions.append(f"Penyakit dominan yang terpantau adalah {top_disease}, sehingga perlu menjadi perhatian dalam monitoring lanjutan.")
    if top_location and top_location != "-":
        conclusions.append(f"Konsentrasi lokasi tertinggi terpantau pada {top_location}, sehingga wilayah tersebut dapat dijadikan prioritas pemantauan.")
    if high_risk_count > 0:
        conclusions.append(f"Terdapat {high_risk_count} signal high-risk yang memerlukan verifikasi, assessment, dan pemantauan prioritas.")
    conclusions.append(f"Kualitas data menunjukkan geocode success rate sebesar {geocode_success_rate}% dan assessment readiness sebesar {assessment_rate}%.")

    return conclusions


def build_report_context(request, forced_scope=None):
    report_type, date_from_obj, date_to_obj = get_date_range_from_request(request)

    selected_disease = request.GET.get("disease", "").strip()
    province_id = request.GET.get("province", "").strip()
    city_id = request.GET.get("city", "").strip()
    report_scope_raw = forced_scope or request.GET.get("report_scope", "general").strip()
    report_scope = normalize_report_scope(report_scope_raw, selected_disease, province_id, city_id)

    status_filter, selected_statuses, status_label = get_report_status_config(request)

    seed_qs = (
        Signal.objects.filter(
            published_at__date__gte=date_from_obj,
            published_at__date__lte=date_to_obj,
            status__in=selected_statuses,
        )
        .exclude(status="noise")
        .select_related("source", "validated_by")
    )

    report_ready, missing_filters = get_report_readiness(report_scope, selected_disease, province_id, city_id)
    selection_disease_recap, selection_province_recap, selection_location_recap = build_scope_landing_data(seed_qs)

    if report_ready:
        base_qs = apply_report_dynamic_filters(seed_qs, selected_disease, province_id, city_id)
    else:
        # Specific reports should not fall back to all data.
        # This avoids Disease/Region/Targeted pages looking like General Report.
        base_qs = seed_qs.none()

    total_signals = base_qs.count()
    raw_count = base_qs.filter(status="raw").count()
    validated_count = base_qs.filter(status="validated").count()
    approved_count = base_qs.filter(status="approved").count()

    high_risk_count = base_qs.filter(threat_score__gte=70).count()
    medium_risk_count = base_qs.filter(threat_score__gte=40, threat_score__lt=70).count()
    low_risk_count = base_qs.filter(threat_score__lt=40).count()

    geocode_ok_statuses = ["OK", "ok", "matched", "manual", "gazetteer_only", "MANUAL"]
    geocode_ok_count = base_qs.filter(geocode_status__in=geocode_ok_statuses).count()

    assessment_ready_count = base_qs.exclude(assessment_summary="").count()
    assessment_pending_count = total_signals - assessment_ready_count

    high_risk_rate = round((high_risk_count / total_signals) * 100, 2) if total_signals else 0
    geocode_success_rate = round((geocode_ok_count / total_signals) * 100, 2) if total_signals else 0
    assessment_rate = round((assessment_ready_count / total_signals) * 100, 2) if total_signals else 0
    avg_score = round(base_qs.aggregate(avg_score=Avg("threat_score"))["avg_score"] or 0, 2)

    disease_recap = list(
        base_qs.exclude(disease_tag="")
        .values("disease_tag")
        .annotate(
            total=Count("id", distinct=True),
            avg_score=Avg("threat_score"),
            high_risk_total=Count("id", filter=Q(threat_score__gte=70), distinct=True),
        )
        .order_by("-total", "-avg_score", "disease_tag")
    )

    province_recap, location_recap = build_location_recap(base_qs, limit=25)
    priority_signals = build_priority_signals(base_qs)
    recent_signals = base_qs.order_by("-published_at", "-created_at")[:75]

    top_disease = disease_recap[0]["disease_tag"] if disease_recap else "-"
    top_location = location_recap[0]["location_name"] if location_recap else "-"
    top_province = province_recap[0]["province_name"] if province_recap else "-"

    region_label = get_location_filter_label(province_id, city_id)
    report_title = build_report_title(report_scope, selected_disease, region_label)

    executive_summary = build_dynamic_executive_summary(
        report_scope=report_scope,
        report_title=report_title,
        date_from_obj=date_from_obj,
        date_to_obj=date_to_obj,
        total_signals=total_signals,
        high_risk_count=high_risk_count,
        avg_score=avg_score,
        top_disease=top_disease,
        top_location=top_location,
        top_province=top_province,
        geocode_success_rate=geocode_success_rate,
        assessment_rate=assessment_rate,
        status_label=status_label,
    )

    focus_items = build_focus_period(disease_recap, location_recap, high_risk_count, assessment_pending_count)
    disease_analysis = build_disease_analysis(disease_recap, base_qs)
    policy_recommendations = build_policy_recommendations(
        report_scope, disease_recap, location_recap, high_risk_count,
        assessment_pending_count, selected_disease, region_label
    )
    report_conclusions = build_report_conclusions(
        total_signals, high_risk_count, top_disease, top_location,
        geocode_success_rate, assessment_rate, report_scope, report_title
    )
    chart_data = build_report_charts(base_qs, disease_recap, location_recap, report_scope)
    map_data = build_report_map_data(base_qs)

    affected_disease_count = len(disease_recap)
    affected_province_count = len(province_recap)
    affected_location_count = len(location_recap)
    top_priority_signal = priority_signals[0] if priority_signals else None
    dominant_disease_risk = classify_report_risk(
        disease_recap[0].get("avg_score", 0) if disease_recap else 0,
        disease_recap[0].get("high_risk_total", 0) if disease_recap else 0,
        disease_recap[0].get("total", 0) if disease_recap else 0,
    ) if disease_recap else "-"
    dominant_location_risk = classify_report_risk(
        location_recap[0].get("avg_score", 0) if location_recap else 0,
        location_recap[0].get("high_risk_total", 0) if location_recap else 0,
        location_recap[0].get("total", 0) if location_recap else 0,
    ) if location_recap else "-"

    disease_choices = (
        Signal.objects.exclude(status="noise")
        .exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .values_list("disease_tag", flat=True)
        .distinct()
        .order_by("disease_tag")
    )

    provinces = Location.objects.filter(level="province", is_active=True, is_false_positive=False).order_by("display_name", "name")
    cities = Location.objects.filter(level__in=["city", "regency"], is_active=True, is_false_positive=False).select_related("parent").order_by("display_name", "name")

    if province_id:
        cities = cities.filter(parent_id=province_id)

    return {
        "page_title": report_title,
        "report_title": report_title,
        "report_scope": report_scope,
        "report_ready": report_ready,
        "missing_filters": missing_filters,
        "selection_disease_recap": selection_disease_recap,
        "selection_province_recap": selection_province_recap,
        "selection_location_recap": selection_location_recap,
        "report_type": report_type,
        "status_filter": status_filter,
        "status_label": status_label,
        "selected_disease": selected_disease,
        "province_id": province_id,
        "city_id": city_id,
        "region_label": region_label,
        "date_from": date_from_obj.isoformat(),
        "date_to": date_to_obj.isoformat(),
        "total_signals": total_signals,
        "raw_count": raw_count,
        "validated_count": validated_count,
        "approved_count": approved_count,
        "high_risk_count": high_risk_count,
        "medium_risk_count": medium_risk_count,
        "low_risk_count": low_risk_count,
        "geocode_ok_count": geocode_ok_count,
        "assessment_ready_count": assessment_ready_count,
        "assessment_pending_count": assessment_pending_count,
        "high_risk_rate": high_risk_rate,
        "geocode_success_rate": geocode_success_rate,
        "assessment_rate": assessment_rate,
        "avg_score": avg_score,
        "executive_summary": executive_summary,
        "focus_items": focus_items,
        "policy_recommendations": policy_recommendations,
        "disease_analysis": disease_analysis,
        "priority_signals": priority_signals,
        "report_conclusions": report_conclusions,
        "disease_recap": disease_recap,
        "province_recap": province_recap,
        "location_recap": location_recap,
        "recent_signals": recent_signals,
        "affected_disease_count": affected_disease_count,
        "affected_province_count": affected_province_count,
        "affected_location_count": affected_location_count,
        "top_priority_signal": top_priority_signal,
        "dominant_disease_risk": dominant_disease_risk,
        "dominant_location_risk": dominant_location_risk,
        "disease_choices": disease_choices,
        "provinces": provinces,
        "cities": cities,
        "chart_data_json": json.dumps(chart_data, default=str),
        "map_data_json": json.dumps(map_data, default=str),
    }


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def report_general(request):
    context = build_report_context(request, forced_scope="general")
    return render(request, "intel/reports/general_report.html", context)


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def report_disease(request):
    context = build_report_context(request, forced_scope="disease")
    return render(request, "intel/reports/disease_report.html", context)


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def report_region(request):
    context = build_report_context(request, forced_scope="region")
    return render(request, "intel/reports/region_report.html", context)


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def report_disease_region(request):
    context = build_report_context(request, forced_scope="disease_region")
    return render(request, "intel/reports/disease_region_report.html", context)


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def reports_generator(request):
    selected_disease = request.GET.get("disease", "").strip()
    province_id = request.GET.get("province", "").strip()
    city_id = request.GET.get("city", "").strip()
    report_scope_raw = request.GET.get("report_scope", "general").strip()
    report_scope = normalize_report_scope(report_scope_raw, selected_disease, province_id, city_id)

    template_map = {
        "general": "intel/reports/general_report.html",
        "disease": "intel/reports/disease_report.html",
        "region": "intel/reports/region_report.html",
        "disease_region": "intel/reports/disease_region_report.html",
    }
    context = build_report_context(request, forced_scope=report_scope)
    return render(request, template_map.get(report_scope, "intel/reports/general_report.html"), context)


@login_required
def export_signals_csv(request):
    report_type, date_from_obj, date_to_obj = get_date_range_from_request(request)

    selected_disease = request.GET.get("disease", "").strip()
    province_id = request.GET.get("province", "").strip()
    city_id = request.GET.get("city", "").strip()
    status_filter, selected_statuses, _ = get_report_status_config(request)

    qs = (
        Signal.objects.filter(
            published_at__date__gte=date_from_obj,
            published_at__date__lte=date_to_obj,
            status__in=selected_statuses,
        )
        .exclude(status="noise")
        .select_related("source", "validated_by")
        .order_by("-published_at", "-created_at")
    )

    qs = apply_report_dynamic_filters(qs, selected_disease, province_id, city_id)

    response = HttpResponse(content_type="text/csv")
    filename = f"signals_{status_filter}_{report_type}_{date_from_obj.isoformat()}_{date_to_obj.isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        "id", "title", "source", "source_url", "published_at", "disease_tag",
        "threat_score", "raw_location_text", "geocode_status", "status",
        "approved_for_mapping", "is_high_risk", "assessment_status",
        "assessment_summary", "validated_by", "validated_at",
    ])

    for signal in qs:
        writer.writerow([
            signal.id,
            signal.title,
            signal.source.name if signal.source else "",
            signal.source_url,
            signal.published_at.isoformat() if signal.published_at else "",
            signal.disease_tag,
            signal.threat_score,
            signal.raw_location_text,
            signal.geocode_status,
            signal.status,
            signal.approved_for_mapping,
            signal.is_high_risk,
            getattr(signal, "assessment_status", ""),
            getattr(signal, "assessment_summary", ""),
            str(signal.validated_by) if signal.validated_by else "",
            signal.validated_at.isoformat() if signal.validated_at else "",
        ])

    return response


def get_signal_triage_flag(signal):
    """
    Menentukan prioritas tindak lanjut analis untuk Raw Signal.
    Tidak disimpan ke database, hanya dihitung saat halaman dibuka.
    """

    assessment_status = signal.assessment_status or ""
    geocode_status = signal.geocode_status or ""
    threat_score = signal.threat_score or 0
    assessment_summary = signal.assessment_summary or ""
    assessment_error = signal.assessment_error or ""

    if threat_score >= 70:
        return {
            "code": "high_risk",
            "label": "High Risk",
            "class": "triage-high",
            "reason": "Skor ancaman tinggi dan perlu diprioritaskan analis."
        }

    if assessment_status in ["failed", "fallback"] or assessment_error:
        return {
            "code": "assessment_failed",
            "label": "Perlu Re-assessment",
            "class": "triage-red",
            "reason": "Assessment gagal atau hanya memakai fallback."
        }

    if not assessment_summary:
        return {
            "code": "needs_assessment",
            "label": "Belum Assessment",
            "class": "triage-yellow",
            "reason": "Signal belum memiliki assessment 5W+1H."
        }

    if geocode_status in [
        "empty",
        "empty_loc",
        "unmatched",
        "not_found",
        "skip_low_conf",
        "skip_too_general",
        "net_err",
    ]:
        return {
            "code": "needs_location",
            "label": "Perlu Koreksi Lokasi",
            "class": "triage-orange",
            "reason": "Lokasi belum berhasil dipetakan secara meyakinkan."
        }

    return {
        "code": "normal",
        "label": "Normal",
        "class": "triage-normal",
        "reason": "Signal sudah relatif lengkap untuk proses validasi."
    }



def normalize_duplicate_text(value):
    value = value or ""
    value = str(value).lower().strip()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def duplicate_title_similarity(title_a, title_b):
    a = normalize_duplicate_text(title_a)
    b = normalize_duplicate_text(title_b)

    if not a or not b:
        return 0.0

    return round(SequenceMatcher(None, a, b).ratio() * 100, 2)


def get_primary_location_ids_for_signal(signal):
    ids = []

    for item in getattr(signal, "primary_locations", []):
        if item.location_id:
            ids.append(item.location_id)

    if not ids:
        ids = list(
            SignalLocation.objects.filter(
                signal=signal,
                is_primary=True,
                location__isnull=False,
            ).values_list("location_id", flat=True)
        )

    return ids

def normalize_duplicate_location_value(value):
    value = value or ""
    value = str(value).lower().strip()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def get_duplicate_location_profile(signal):
    """
    Profil lokasi untuk duplicate review.

    Prinsip:
    - Lokasi terstruktur/admin dipakai sebagai pembanding kuat.
    - raw_location_text juga dianggap lokasi apabila tidak kosong.
    - Lokasi benar-benar kosong tetap boleh masuk review.
    - Lokasi mentah yang berbeda tidak boleh otomatis dianggap kosong.
    """

    profile = {
        "location_ids": set(),
        "city_codes": set(),
        "city_names": set(),
        "province_codes": set(),
        "province_names": set(),
        "raw_names": set(),
        "has_structured_location": False,
        "has_any_location": False,
    }

    primary_locations = list(getattr(signal, "primary_locations", []) or [])

    if not primary_locations:
        primary_locations = list(
            SignalLocation.objects.filter(
                signal=signal,
                is_primary=True,
                location__isnull=False,
            ).select_related("location", "location__parent")
        )

    for item in primary_locations:
        loc = getattr(item, "location", None)

        if not loc:
            continue

        profile["has_structured_location"] = True
        profile["has_any_location"] = True
        profile["location_ids"].add(loc.id)

        loc_name = normalize_duplicate_location_value(
            loc.display_name or loc.name
        )

        if loc.level in ["city", "regency"]:
            if loc.city_regency_code:
                profile["city_codes"].add(loc.city_regency_code)

            if loc_name:
                profile["city_names"].add(loc_name)

            if loc.province_code:
                profile["province_codes"].add(loc.province_code)

            if loc.parent:
                parent_name = normalize_duplicate_location_value(
                    loc.parent.display_name or loc.parent.name
                )

                if parent_name:
                    profile["province_names"].add(parent_name)

                if loc.parent.province_code:
                    profile["province_codes"].add(loc.parent.province_code)

        elif loc.level == "province":
            if loc.province_code:
                profile["province_codes"].add(loc.province_code)

            if loc_name:
                profile["province_names"].add(loc_name)

    admin_kabkota = normalize_duplicate_location_value(
        getattr(signal, "admin_kabkota", "") or ""
    )
    admin_province = normalize_duplicate_location_value(
        getattr(signal, "admin_province", "") or ""
    )
    raw_location = normalize_duplicate_location_value(
        getattr(signal, "raw_location_text", "") or ""
    )

    if admin_kabkota:
        profile["city_names"].add(admin_kabkota)
        profile["has_structured_location"] = True
        profile["has_any_location"] = True

    if admin_province:
        profile["province_names"].add(admin_province)
        profile["has_structured_location"] = True
        profile["has_any_location"] = True

    if raw_location:
        profile["raw_names"].add(raw_location)
        profile["has_any_location"] = True

    return profile

def duplicate_has_city_level_info(profile):
    """
    True jika signal punya informasi lokasi level kabupaten/kota.

    Dipakai agar potensi duplikat tidak berhenti di level provinsi.
    Contoh: Kota Batu vs Pamekasan sama-sama Jawa Timur, tetapi bukan lokasi sama.
    """

    return bool(
        profile.get("location_ids")
        or profile.get("city_codes")
        or profile.get("city_names")
    )


def duplicate_location_overlap(profile_a, profile_b):
    """
    True kalau dua signal punya indikasi lokasi sama.

    Prinsip:
    - Jika dua signal sama-sama punya kabupaten/kota, overlap hanya dihitung
      jika kabupaten/kotanya sama.
    - Provinsi yang sama tidak otomatis dianggap lokasi sama apabila
      kabupaten/kota keduanya sudah jelas.
    - Jika salah satu signal hanya punya provinsi atau lokasi kosong, provinsi
      masih boleh menjadi indikasi overlap lemah untuk review manual.
    """

    a_has_city = duplicate_has_city_level_info(profile_a)
    b_has_city = duplicate_has_city_level_info(profile_b)

    city_pairs = [
        ("location_ids", "location_ids"),
        ("city_codes", "city_codes"),
        ("city_names", "city_names"),
        ("raw_names", "raw_names"),
    ]

    for left_key, right_key in city_pairs:
        if profile_a[left_key] and profile_b[right_key]:
            if profile_a[left_key].intersection(profile_b[right_key]):
                return True

    # Jika keduanya sudah punya kab/kota, jangan pakai provinsi sebagai overlap.
    # Ini mencegah Kota Batu vs Pamekasan tetap dihitung duplikat hanya karena sama-sama Jawa Timur.
    if a_has_city and b_has_city:
        return False

    province_pairs = [
        ("province_codes", "province_codes"),
        ("province_names", "province_names"),
    ]

    for left_key, right_key in province_pairs:
        if profile_a[left_key] and profile_b[right_key]:
            if profile_a[left_key].intersection(profile_b[right_key]):
                return True

    return False


def duplicate_raw_location_related(profile_a, profile_b):
    """
    Mengecek apakah raw location masih mungkin saling terkait.

    Contoh terkait:
    - "bangka tengah" vs "bangka tengah kepulauan bangka belitung"
    - "kota kupang" vs "kupang"

    Contoh tidak terkait:
    - "bangka tengah" vs "dusun randualas"
    - "riau" vs "nusa tenggara timur"
    """

    if not profile_a["raw_names"] or not profile_b["raw_names"]:
        return False

    for raw_a in profile_a["raw_names"]:
        for raw_b in profile_b["raw_names"]:
            if raw_a == raw_b:
                return True

            if raw_a in raw_b or raw_b in raw_a:
                return True

    return False

def duplicate_locations_are_definitely_different(profile_a, profile_b):
    """
    True hanya kalau dua signal sama-sama punya informasi lokasi,
    tetapi wilayahnya berbeda.

    Penting:
    - Lokasi benar-benar kosong tetap boleh masuk kandidat.
    - Lokasi mentah yang tidak kosong tidak dianggap kosong.
    - Jadi Bangka Tengah vs Dusun Randualas tidak masuk potensi duplikat.
    """

    # Kalau salah satu benar-benar tidak punya lokasi sama sekali,
    # jangan dibuang. Ini memenuhi syarat: lokasi kosong tetap masuk review.
    if not profile_a["has_any_location"]:
        return False

    if not profile_b["has_any_location"]:
        return False

    # Kalau ada overlap lokasi level kab/kota/provinsi yang valid, jangan dianggap beda.
    if duplicate_location_overlap(profile_a, profile_b):
        return False

    if duplicate_raw_location_related(profile_a, profile_b):
        return False

    a_has_city = duplicate_has_city_level_info(profile_a)
    b_has_city = duplicate_has_city_level_info(profile_b)

    # Jika keduanya punya informasi kab/kota tetapi tidak overlap,
    # anggap beda lokasi meskipun provinsinya sama.
    # Contoh: Kota Batu vs Pamekasan sama-sama Jawa Timur, tetapi bukan duplikat lokasi.
    if a_has_city and b_has_city:
        return True

    # Jika keduanya punya kode kab/kota dan tidak beririsan, beda pasti.
    if profile_a["city_codes"] and profile_b["city_codes"]:
        return True

    # Jika keduanya punya ID lokasi primary dan tidak beririsan,
    # cek dulu apakah masih satu provinsi untuk kasus salah satu belum spesifik.
    if profile_a["location_ids"] and profile_b["location_ids"]:
        if profile_a["province_codes"] and profile_b["province_codes"]:
            if profile_a["province_codes"].intersection(profile_b["province_codes"]):
                return False
        return True

    # Jika keduanya punya provinsi jelas dan provinsinya berbeda, beda pasti.
    if profile_a["province_codes"] and profile_b["province_codes"]:
        return True

    if profile_a["province_names"] and profile_b["province_names"]:
        return True

    # Kasus penting:
    # satu signal punya lokasi struktur/admin, kandidat hanya punya raw location,
    # dan raw tersebut tidak related dengan lokasi utama.
    # Contoh: Bangka Tengah vs Dusun Randualas.
    structured_keys = [
        "city_names",
        "province_names",
    ]

    if profile_a["has_structured_location"] and profile_b["raw_names"]:
        known_names = set()
        for key in structured_keys:
            known_names.update(profile_a[key])

        for known in known_names:
            for raw_b in profile_b["raw_names"]:
                if known and raw_b and (known in raw_b or raw_b in known):
                    return False

        return True

    if profile_b["has_structured_location"] and profile_a["raw_names"]:
        known_names = set()
        for key in structured_keys:
            known_names.update(profile_b[key])

        for known in known_names:
            for raw_a in profile_a["raw_names"]:
                if known and raw_a and (known in raw_a or raw_a in known):
                    return False

        return True

    # Kalau keduanya hanya punya raw location dan raw berbeda,
    # anggap beda lokasi, bukan lokasi kosong.
    if profile_a["raw_names"] and profile_b["raw_names"]:
        return True

    return False
    

def get_duplicate_candidates(signal, days=7, limit=30):
    """
    Cari kandidat duplikat berbasis:
    - URL sama / mirip
    - disease sama
    - lokasi sama atau lokasi belum jelas
    - rentang tanggal dekat
    - kemiripan judul

    Perbaikan logic lokasi:
    - Jika dua signal sama-sama punya lokasi jelas dan lokasinya berbeda pasti,
      kandidat dibuang dari potensi duplikat.
    - Jika salah satu lokasi kosong / belum jelas, kandidat tetap boleh masuk.
    - Ini mencegah kasus DBD NTT vs DBD Riau ikut dihitung sebagai potensi duplikat.
    """

    primary_locations = SignalLocation.objects.filter(is_primary=True).select_related(
        "location",
        "location__parent",
    )

    qs = (
        Signal.objects.select_related("source", "cluster")
        .prefetch_related(
            Prefetch(
                "locations",
                queryset=primary_locations,
                to_attr="primary_locations",
            )
        )
        .exclude(id=signal.id)
        .exclude(status="noise")
    )

    if signal.disease_tag:
        qs = qs.filter(disease_tag__iexact=signal.disease_tag)

    if signal.published_at:
        qs = qs.filter(
            published_at__range=(
                signal.published_at - timedelta(days=days),
                signal.published_at + timedelta(days=days),
            )
        )

    qs = qs.distinct().order_by("-published_at", "-created_at", "-id")[:300]

    candidates = []

    signal_title = signal.title or ""
    signal_source_url = signal.source_url or ""
    signal_resolved_url = getattr(signal, "resolved_url", "") or ""

    signal_location_profile = get_duplicate_location_profile(signal)

    for item in qs:
        item_location_profile = get_duplicate_location_profile(item)

        if duplicate_locations_are_definitely_different(
            signal_location_profile,
            item_location_profile,
        ):
            continue

        score = 0
        reasons = []

        title_sim = duplicate_title_similarity(signal_title, item.title)

        if title_sim >= 85:
            score += 35
            reasons.append(f"Judul sangat mirip ({title_sim}%).")
        elif title_sim >= 60:
            score += 20
            reasons.append(f"Judul cukup mirip ({title_sim}%).")
        elif title_sim >= 40:
            score += 10
            reasons.append(f"Judul agak mirip ({title_sim}%).")

        item_source_url = item.source_url or ""
        item_resolved_url = getattr(item, "resolved_url", "") or ""

        if signal_source_url and (
            signal_source_url == item_source_url
            or signal_source_url == item_resolved_url
        ):
            score += 50
            reasons.append("URL sumber sama dengan signal utama.")

        if signal_resolved_url and (
            signal_resolved_url == item_source_url
            or signal_resolved_url == item_resolved_url
        ):
            score += 50
            reasons.append("Resolved URL sama dengan signal utama.")

        if (
            signal.disease_tag
            and item.disease_tag
            and signal.disease_tag.lower() == item.disease_tag.lower()
        ):
            score += 15
            reasons.append("Kategori penyakit sama.")

        same_location = duplicate_location_overlap(
            signal_location_profile,
            item_location_profile,
        )

        if same_location:
            score += 25
            reasons.append("Lokasi sama atau mengarah ke wilayah yang sama.")
        else:
            if (
                not signal_location_profile["has_structured_location"]
                or not item_location_profile["has_structured_location"]
            ):
                reasons.append("Lokasi salah satu signal belum jelas, tetap perlu review manual.")

        if signal.published_at and item.published_at:
            diff_days = abs((signal.published_at.date() - item.published_at.date()).days)

            if diff_days <= 1:
                score += 15
                reasons.append("Tanggal publikasi sangat dekat.")
            elif diff_days <= 3:
                score += 10
                reasons.append("Tanggal publikasi dekat.")
            elif diff_days <= days:
                score += 5
                reasons.append("Tanggal publikasi masih dalam rentang review.")

        score = min(score, 100)

        if score >= 80:
            label = "Kemungkinan Duplikat Tinggi"
            label_class = "dup-high"
        elif score >= 55:
            label = "Perlu Review"
            label_class = "dup-medium"
        else:
            label = "Kemiripan Rendah"
            label_class = "dup-low"

        item.duplicate_score = score
        item.duplicate_label = label
        item.duplicate_label_class = label_class
        item.title_similarity_score = title_sim
        item.duplicate_reasons = reasons

        if score >= 35:
            candidates.append(item)

    candidates.sort(key=lambda x: x.duplicate_score, reverse=True)

    return candidates[:limit]

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def signal_duplicate_review(request, pk):
    primary_locations = SignalLocation.objects.filter(is_primary=True).select_related(
        "location",
        "location__parent",
    )

    signal = get_object_or_404(
        Signal.objects.select_related("source")
        .prefetch_related(
            Prefetch(
                "locations",
                queryset=primary_locations,
                to_attr="primary_locations",
            )
        ),
        pk=pk,
    )

    def _mark_noise(item, note):
        before_data = {"status": item.status, "approved_for_mapping": item.approved_for_mapping}
        item.status = "noise"
        item.approved_for_mapping = False
        item.validated_by = request.user
        item.validated_at = timezone.now()
        item.save(update_fields=["status", "approved_for_mapping", "validated_by", "validated_at", "updated_at"])
        AuditLog.objects.create(
            user=request.user,
            action="mark_noise",
            model_name="Signal",
            object_id=str(item.id),
            notes=note,
            before_data=before_data,
            after_data={"status": item.status, "approved_for_mapping": item.approved_for_mapping, "duplicate_review_main_signal_id": signal.id},
        )

    def _mark_validated(item, note):
        before_data = {"status": item.status, "approved_for_mapping": item.approved_for_mapping}
        item.status = "validated"
        item.approved_for_mapping = False
        item.validated_by = request.user
        item.validated_at = timezone.now()
        item.save(update_fields=["status", "approved_for_mapping", "validated_by", "validated_at", "updated_at"])
        AuditLog.objects.create(
            user=request.user,
            action="validate",
            model_name="Signal",
            object_id=str(item.id),
            notes=note,
            before_data=before_data,
            after_data={"status": item.status, "approved_for_mapping": item.approved_for_mapping, "duplicate_review_main_signal_id": signal.id},
        )

    if request.method == "POST":
        action = request.POST.get("action", "").strip()

        # Ambil hanya ID checkbox yang benar-benar dikirim dari form.
        # Dedupe dilakukan agar satu kandidat tidak diproses dua kali apabila ada input ganda.
        selected_ids = []
        for item in request.POST.getlist("selected_ids"):
            item = str(item).strip()
            if item.isdigit() and item not in selected_ids:
                selected_ids.append(item)

        target_id = (request.POST.get("target_id") or request.POST.get("single_id") or "").strip()
        single_ids = [target_id] if target_id.isdigit() else []

        bulk_actions = {"mark_selected_noise", "mark_selected_valid", "mark_selected_valid_update", "validate_main_and_noise_selected", "noise_main_and_noise_selected"}
        single_actions = {"mark_one_noise", "mark_single_noise", "mark_one_valid", "mark_single_valid_update"}

        if action in bulk_actions and not selected_ids:
            messages.warning(request, "Pilih minimal 1 kandidat terlebih dahulu.")
            return redirect("intel:signal_duplicate_review", pk=signal.id)

        if action in single_actions and not single_ids:
            messages.warning(request, "Target kandidat tidak ditemukan.")
            return redirect("intel:signal_duplicate_review", pk=signal.id)

        target_ids = single_ids if action in single_actions else selected_ids

        # Pengaman khusus duplicate review:
        # untuk aksi bulk, hanya kandidat yang termasuk potensi duplikat saat halaman ini dibuka
        # dan ID-nya benar-benar ada di checkbox terpilih yang boleh diproses.
        # Ini mencegah action "noise utama + kandidat terpilih" mengenai kandidat lain
        # yang tidak dicentang / ID stray dari form.
        if action in bulk_actions:
            allowed_candidate_ids = {
                str(item.id) for item in get_duplicate_candidates(signal, days=7, limit=30)
            }
            target_ids = [item for item in target_ids if item in allowed_candidate_ids]

            if not target_ids:
                messages.warning(request, "Tidak ada kandidat terpilih yang valid untuk diproses.")
                return redirect("intel:signal_duplicate_review", pk=signal.id)

        candidates_qs = Signal.objects.filter(id__in=target_ids).exclude(id=signal.id)

        if action == "mark_selected_noise":
            changed_count = 0
            for item in candidates_qs:
                if item.status == "noise":
                    continue
                _mark_noise(item, note=f"Marked as duplicate/noise from duplicate review. Main signal id={signal.id}")
                changed_count += 1
            messages.success(request, f"{changed_count} kandidat duplikat ditandai sebagai noise.")
            return redirect("intel:signal_duplicate_review", pk=signal.id)

        elif action == "noise_main_and_noise_selected":
            _mark_noise(signal, note="Main signal marked as noise from duplicate review and selected candidates marked as noise.")
            changed_count = 0
            for item in candidates_qs:
                if item.status == "noise":
                    continue
                _mark_noise(item, note=f"Marked as noise together with main signal from duplicate review. Main signal id={signal.id}")
                changed_count += 1
            messages.success(request, f"Signal utama dan {changed_count} kandidat terpilih ditandai sebagai noise.")
            return redirect("intel:raw_signals")

        elif action in {"mark_selected_valid", "mark_selected_valid_update"}:
            changed_count = 0
            for item in candidates_qs:
                if item.status == "noise":
                    continue
                _mark_validated(item, note=f"Candidate marked as valid/update from duplicate review. Main signal id={signal.id}")
                changed_count += 1
            messages.success(request, f"{changed_count} kandidat ditandai sebagai validated/update, bukan noise.")
            return redirect("intel:signal_duplicate_review", pk=signal.id)

        elif action in {"mark_one_noise", "mark_single_noise"}:
            item = candidates_qs.first()
            if not item:
                messages.warning(request, "Kandidat tidak ditemukan.")
                return redirect("intel:signal_duplicate_review", pk=signal.id)
            _mark_noise(item, note=f"Single candidate marked as duplicate/noise from duplicate review. Main signal id={signal.id}")
            messages.success(request, f"Kandidat #{item.id} ditandai sebagai noise.")
            return redirect("intel:signal_duplicate_review", pk=signal.id)

        elif action in {"mark_one_valid", "mark_single_valid_update"}:
            item = candidates_qs.first()
            if not item:
                messages.warning(request, "Kandidat tidak ditemukan.")
                return redirect("intel:signal_duplicate_review", pk=signal.id)
            _mark_validated(item, note=f"Single candidate marked as valid/update from duplicate review. Main signal id={signal.id}")
            messages.success(request, f"Kandidat #{item.id} ditandai sebagai validated/update.")
            return redirect("intel:signal_duplicate_review", pk=signal.id)

        elif action == "mark_current_valid":
            _mark_validated(signal, note="Main signal marked as validated from duplicate review.")
            messages.success(request, "Signal utama ditandai sebagai validated.")
            return redirect("intel:signal_duplicate_review", pk=signal.id)

        elif action == "mark_current_noise":
            _mark_noise(signal, note="Main signal marked as noise from duplicate review.")
            messages.success(request, "Signal utama ditandai sebagai noise.")
            return redirect("intel:raw_signals")

        elif action == "validate_main_and_noise_selected":
            _mark_validated(signal, note="Main signal marked as validated from duplicate review and selected candidates marked as noise.")
            changed_count = 0
            for item in candidates_qs:
                if item.status == "noise":
                    continue
                _mark_noise(item, note=f"Marked as duplicate/noise after validating main signal. Main signal id={signal.id}")
                changed_count += 1
            messages.success(request, f"Signal utama divalidasi dan {changed_count} kandidat terpilih ditandai sebagai noise.")
            return redirect("intel:signal_duplicate_review", pk=signal.id)

        else:
            messages.warning(request, "Tidak ada aksi yang dipilih.")
            return redirect("intel:signal_duplicate_review", pk=signal.id)

    candidates = get_duplicate_candidates(signal, days=7, limit=30)
    return render(request, "intel/signal_duplicate_review.html", {
        "page_title": "Duplicate Review",
        "signal": signal,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "current_path": request.get_full_path(),
    })

def attach_duplicate_warning(signal, days=7, limit=30):
    candidates = get_duplicate_candidates(signal, days=days, limit=limit)
    signal.duplicate_count = len(candidates)
    signal.has_duplicate_warning = signal.duplicate_count > 0
    return signal

@login_required
@role_required(ROLE_ADMIN)
def production_signal_debug(request):
    raw_validated_qs = Signal.objects.filter(status__in=["raw", "validated"])
    approved_qs = Signal.objects.filter(status="approved")

    data = {
        "database_engine": settings.DATABASES["default"].get("ENGINE"),
        "database_name": str(settings.DATABASES["default"].get("NAME")),
        "has_database_url": bool(os.environ.get("DATABASE_URL")),
        "total_signal": Signal.objects.count(),
        "raw_validated_count": raw_validated_qs.count(),
        "approved_count": approved_qs.count(),
        "noise_count": Signal.objects.filter(status="noise").count(),
        "status_breakdown": list(
            Signal.objects.values("status")
            .annotate(total=Count("id"))
            .order_by("status")
        ),
        "latest_raw_validated": list(
            raw_validated_qs.order_by("-id")
            .values("id", "title", "status", "published_at")[:10]
        ),
        "latest_approved": list(
            approved_qs.order_by("-id")
            .values("id", "title", "status", "published_at")[:10]
        ),
    }

    return JsonResponse(data, json_dumps_params={"indent": 2, "default": str})


# =========================================================
# RAW SIGNAL + CLUSTER ANALYST WORKFLOW HELPERS
# =========================================================
RAW_WORKFLOW_GEOCODE_OK_STATUSES = ["OK", "ok", "matched", "manual", "gazetteer_only", "MANUAL"]
RAW_WORKFLOW_LOCATION_PROBLEM_STATUSES = [
    "empty", "empty_loc", "unmatched", "not_found", "skip_low_conf",
    "skip_too_general", "net_err", "EMPTY_LOC", "NOT_FOUND", "NET_ERR",
    "SKIP_LOW_CONF", "SKIP_TOO_GENERAL", "PENDING", "pending",
]


def build_raw_workflow_stats(base_qs=None):
    """
    Ringkasan antrean kerja analis pada Raw Signals.
    Tidak membuat halaman baru agar workflow tetap terpusat pada Raw Signal + Cluster.
    """
    if base_qs is None:
        base_qs = Signal.objects.filter(status__in=["raw", "validated"])

    base_qs = base_qs.exclude(status="noise")

    needs_assessment_q = (
        Q(assessment_summary__isnull=True) |
        Q(assessment_summary="") |
        Q(assessment_status__in=["pending", "not_generated", ""])
    )
    assessment_failed_q = (
        Q(assessment_status__in=["failed", "fallback"]) |
        (Q(assessment_error__isnull=False) & ~Q(assessment_error=""))
    )
    needs_location_q = (
        Q(geocode_status__in=RAW_WORKFLOW_LOCATION_PROBLEM_STATUSES) |
        Q(locations__isnull=True)
    )
    ready_validation_q = (
        Q(status="raw") &
        ~Q(assessment_summary="") &
        Q(assessment_summary__isnull=False) &
        Q(geocode_status__in=RAW_WORKFLOW_GEOCODE_OK_STATUSES) &
        ~Q(approval_recommendation__in=["noise", "duplicate", "fix_location"])
    )
    ready_approval_q = (
        Q(status="validated") &
        Q(geocode_status__in=RAW_WORKFLOW_GEOCODE_OK_STATUSES) &
        (Q(approval_recommendation="approve") | Q(confidence_score__gte=60))
    )

    return {
        "total_queue": base_qs.distinct().count(),
        "high_risk": base_qs.filter(threat_score__gte=70).distinct().count(),
        "needs_assessment": base_qs.filter(needs_assessment_q).distinct().count(),
        "assessment_failed": base_qs.filter(assessment_failed_q).distinct().count(),
        "needs_location": base_qs.filter(needs_location_q).distinct().count(),
        "duplicate_candidate": base_qs.filter(approval_recommendation="duplicate").distinct().count(),
        "ready_validation": base_qs.filter(ready_validation_q).distinct().count(),
        "ready_approval": base_qs.filter(ready_approval_q).distinct().count(),
    }


def get_signal_workflow_action(signal):
    """Label ringkas tindakan berikutnya pada Raw Signal."""
    assessment_summary = (getattr(signal, "assessment_summary", "") or "").strip()
    assessment_status = (getattr(signal, "assessment_status", "") or "").strip()
    assessment_error = (getattr(signal, "assessment_error", "") or "").strip()
    geocode_status = (getattr(signal, "geocode_status", "") or "").strip()
    recommendation = (getattr(signal, "approval_recommendation", "") or "").strip()
    status = (getattr(signal, "status", "") or "").strip()

    if recommendation == "duplicate":
        return {"code": "duplicate", "label": "Review duplikat", "class": "workflow-duplicate"}
    if assessment_status in ["failed", "fallback"] or assessment_error:
        return {"code": "assessment_failed", "label": "Re-assessment", "class": "workflow-warning"}
    if not assessment_summary:
        return {"code": "needs_assessment", "label": "Generate 5W+1H", "class": "workflow-assessment"}
    if geocode_status in RAW_WORKFLOW_LOCATION_PROBLEM_STATUSES:
        return {"code": "needs_location", "label": "Koreksi lokasi", "class": "workflow-location"}
    if status == "raw":
        return {"code": "ready_validation", "label": "Siap validasi", "class": "workflow-valid"}
    if status == "validated":
        return {"code": "ready_approval", "label": "Siap approval", "class": "workflow-approve"}
    return {"code": "monitor", "label": "Monitoring", "class": "workflow-monitor"}


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def raw_signals_list(request):
    search = request.GET.get("search", "").strip()
    disease = request.GET.get("disease", "").strip()
    geocode_status = request.GET.get("geocode_status", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    sort = request.GET.get("sort", "-published_at").strip()
    province_id = request.GET.get("province", "").strip()
    city_id = request.GET.get("city", "").strip()
    assessment = request.GET.get("assessment", "").strip()
    triage = request.GET.get("triage", "").strip()
    triage_priority = request.GET.get("triage_priority", "").strip()
    approval_recommendation = request.GET.get("approval_recommendation", "").strip()
    min_confidence = request.GET.get("min_confidence", "").strip()

    primary_locations = SignalLocation.objects.filter(is_primary=True).select_related(
        "location",
        "location__parent",
    )

    qs = (
        Signal.objects.select_related("source", "cluster")
        .prefetch_related(
            Prefetch(
                "locations",
                queryset=primary_locations,
                to_attr="primary_locations",
            )
        )
        .filter(status__in=["raw", "validated"])
    )

    # Ringkasan antrean kerja dihitung dari seluruh Raw + Validated,
    # tidak mengikuti filter tabel agar analis tetap melihat beban kerja total.
    raw_workflow_stats = build_raw_workflow_stats(
        Signal.objects.filter(status__in=["raw", "validated"])
    )

    # =========================
    # TRIAGE FILTER
    # =========================
    if triage == "high_risk":
        qs = qs.filter(threat_score__gte=70)

    elif triage == "needs_assessment":
        qs = qs.filter(
            Q(assessment_summary__isnull=True) |
            Q(assessment_summary="") |
            Q(assessment_status__in=["pending", "not_generated", ""])
        )

    elif triage == "assessment_failed":
        qs = qs.filter(
            Q(assessment_status__in=["failed", "fallback"]) |
            (
                Q(assessment_error__isnull=False) &
                ~Q(assessment_error="")
            )
        )

    elif triage == "needs_location":
        qs = qs.filter(
            Q(geocode_status__in=[
                "empty",
                "empty_loc",
                "unmatched",
                "not_found",
                "skip_low_conf",
                "skip_too_general",
                "net_err",
            ]) |
            Q(locations__isnull=True)
        ).distinct()

    elif triage == "url_problem":
        qs = qs.filter(
            Q(assessment_status__in=["failed", "fallback"]) |
            (
                Q(assessment_error__isnull=False) &
                ~Q(assessment_error="")
            )
        )

    elif triage == "duplicate_candidate":
        qs = qs.filter(approval_recommendation="duplicate")

    elif triage == "ready_validation":
        qs = qs.filter(
            status="raw",
            geocode_status__in=RAW_WORKFLOW_GEOCODE_OK_STATUSES,
        ).exclude(
            Q(assessment_summary="") |
            Q(assessment_summary__isnull=True) |
            Q(approval_recommendation__in=["noise", "duplicate", "fix_location"])
        )

    elif triage == "ready_approval":
        qs = qs.filter(
            status="validated",
            geocode_status__in=RAW_WORKFLOW_GEOCODE_OK_STATUSES,
        ).filter(
            Q(approval_recommendation="approve") |
            Q(confidence_score__gte=60)
        )

    # =========================
    # DATABASE TRIAGE FILTER
    # =========================
    if triage_priority:
        qs = qs.filter(triage_priority=triage_priority)

    if approval_recommendation:
        qs = qs.filter(approval_recommendation=approval_recommendation)

    if min_confidence:
        try:
            qs = qs.filter(confidence_score__gte=int(min_confidence))
        except ValueError:
            pass

    # =========================
    # SEARCH FILTER
    # =========================
    if search:
        qs = qs.filter(
            Q(title__icontains=search)
            | Q(raw_location_text__icontains=search)
            | Q(source_url__icontains=search)
            | Q(content__icontains=search)
            | Q(source__name__icontains=search)
            | Q(locations__raw_location_text__icontains=search)
            | Q(locations__location__display_name__icontains=search)
            | Q(locations__location__name__icontains=search)
            | Q(locations__location__parent__display_name__icontains=search)
        )

    if disease:
        qs = qs.filter(disease_tag__iexact=disease)

    if geocode_status:
        qs = qs.filter(geocode_status__iexact=geocode_status)

    # =========================
    # ASSESSMENT FILTER
    # =========================
    if assessment == "not_generated":
        qs = qs.filter(
            Q(assessment_summary="") |
            Q(assessment_summary__isnull=True)
        )

    elif assessment == "article_based":
        qs = qs.filter(
            assessment_5w1h__assessment_quality="article_based"
        )

    elif assessment == "article_based_partial":
        qs = qs.filter(
            assessment_5w1h__assessment_quality="article_based_partial"
        )

    elif assessment == "low_fallback":
        qs = qs.filter(
            assessment_5w1h__assessment_quality="low_fallback"
        )

    elif assessment == "failed":
        qs = qs.filter(
            Q(assessment_status="failed") |
            (
                Q(assessment_error__isnull=False) &
                ~Q(assessment_error="")
            )
        )

    # =========================
    # DATE FILTER
    # =========================
    if date_from:
        qs = qs.filter(published_at__date__gte=date_from)

    if date_to:
        qs = qs.filter(published_at__date__lte=date_to)

    # =========================
    # LOCATION FILTER
    # =========================
    if province_id:
        qs = qs.filter(
            Q(locations__is_primary=True, locations__location__parent_id=province_id)
            | Q(
                locations__is_primary=True,
                locations__location_id=province_id,
                locations__location__level="province",
            )
        )

    if city_id:
        qs = qs.filter(
            locations__is_primary=True,
            locations__location_id=city_id,
        )

    # =========================
    # SORTING
    # =========================
    allowed_sort_fields = {
        "id": "id",
        "-id": "-id",
        "published_at": "published_at",
        "-published_at": "-published_at",
        "threat_score": "threat_score",
        "-threat_score": "-threat_score",
        "created_at": "created_at",
        "-created_at": "-created_at",
        "title": "title",
        "-title": "-title",
        "disease_tag": "disease_tag",
        "-disease_tag": "-disease_tag",
        "raw_location_text": "raw_location_text",
        "-raw_location_text": "-raw_location_text",
        "geocode_status": "geocode_status",
        "-geocode_status": "-geocode_status",
        "status": "status",
        "-status": "-status",
        "confidence_score": "confidence_score",
        "-confidence_score": "-confidence_score",
        "triage_priority": "triage_priority",
        "-triage_priority": "-triage_priority",
    }

    sort_field = allowed_sort_fields.get(sort, "-published_at")
    qs = qs.distinct().order_by(sort_field, "-created_at", "-id")

    # =========================
    # PAGINATION
    # =========================
    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # =========================
    # DISPLAY FLAGS
    # =========================
    for signal in page_obj.object_list:
        signal.triage_flag = get_signal_triage_flag(signal)
        signal.workflow_action = get_signal_workflow_action(signal)
        attach_duplicate_warning(signal, days=7, limit=30)

    # =========================
    # FILTER CHOICES
    # =========================
    disease_choices = (
        Signal.objects.exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .values_list("disease_tag", flat=True)
        .distinct()
        .order_by("disease_tag")
    )

    geocode_choices = (
        Signal.objects.exclude(geocode_status="")
        .exclude(geocode_status__isnull=True)
        .values_list("geocode_status", flat=True)
        .distinct()
        .order_by("geocode_status")
    )

    provinces = Location.objects.filter(
        level="province",
        is_active=True,
        is_false_positive=False,
    ).order_by("display_name", "name")

    cities = Location.objects.filter(
        level__in=["city", "regency"],
        is_active=True,
        is_false_positive=False,
    ).select_related("parent").order_by("display_name", "name")

    if province_id:
        cities = cities.filter(parent_id=province_id)

    return render(request, "intel/raw_signals_list.html", {
        "page_title": "Raw Signals",
        "page_obj": page_obj,
        "search": search,
        "date_from": date_from,
        "date_to": date_to,
        "disease": disease,
        "geocode_status": geocode_status,
        "assessment": assessment,
        "triage": triage,
        "triage_priority": triage_priority,
        "approval_recommendation": approval_recommendation,
        "min_confidence": min_confidence,
        "raw_workflow_stats": raw_workflow_stats,
        "triage_priority_choices": Signal.TRIAGE_PRIORITY_CHOICES,
        "approval_recommendation_choices": Signal.APPROVAL_RECOMMENDATION_CHOICES,
        "sort": sort,
        "province_id": province_id,
        "city_id": city_id,
        "disease_choices": disease_choices,
        "geocode_choices": geocode_choices,
        "provinces": provinces,
        "cities": cities,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def cluster_triage_queue(request):
    q = request.GET.get("q", "").strip()
    priority = request.GET.get("priority", "").strip()
    status = request.GET.get("status", "").strip()
    disease = request.GET.get("disease", "").strip()
    location = request.GET.get("location", "").strip()
    min_signals = request.GET.get("min_signals", "").strip()

    clusters = SignalCluster.objects.all().order_by(
        "-max_score",
        "-signal_count",
        "-avg_confidence",
        "-created_at",
    )

    if q:
        clusters = clusters.filter(
            Q(cluster_key__icontains=q)
            | Q(disease_tag__icontains=q)
            | Q(location_name__icontains=q)
            | Q(summary__icontains=q)
            | Q(recommendation__icontains=q)
            | Q(reason__icontains=q)
        )

    if priority:
        clusters = clusters.filter(priority=priority)

    if status:
        clusters = clusters.filter(status=status)

    if disease:
        clusters = clusters.filter(disease_tag__icontains=disease)

    if location:
        clusters = clusters.filter(location_name__icontains=location)

    if min_signals:
        try:
            clusters = clusters.filter(signal_count__gte=int(min_signals))
        except ValueError:
            pass

    stats = {
        "total_clusters": clusters.count(),
        "urgent_clusters": clusters.filter(priority="urgent").count(),
        "high_clusters": clusters.filter(priority="high").count(),
        "ready_clusters": clusters.filter(status="ready_for_approval").count(),
        "unknown_location_clusters": clusters.filter(location_name="unknown_location").count(),
    }

    paginator = Paginator(clusters, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "intel/cluster_triage_queue.html", {
        "page_title": "Cluster Triage Queue",
        "page_obj": page_obj,
        "q": q,
        "priority": priority,
        "status": status,
        "disease": disease,
        "location": location,
        "min_signals": min_signals,
        "priority_choices": SignalCluster.PRIORITY_CHOICES,
        "status_choices": SignalCluster.CLUSTER_STATUS_CHOICES,
        "stats": stats,
    })
    

def _cluster_validate_signal(signal, user, cluster):
    """Validate raw signal from cluster detail. Keeps the existing raw -> validated logic."""
    before_data = {
        "status": signal.status,
        "approved_for_mapping": signal.approved_for_mapping,
    }

    signal.status = "validated"
    signal.validated_by = user
    signal.validated_at = timezone.now()
    signal.save(update_fields=["status", "validated_by", "validated_at", "updated_at"])

    AuditLog.objects.create(
        user=user,
        action="mark_validated",
        model_name="Signal",
        object_id=str(signal.id),
        notes=f"Signal validated from cluster detail. Cluster id={cluster.id}",
        before_data=before_data,
        after_data={
            "status": signal.status,
            "approved_for_mapping": signal.approved_for_mapping,
            "cluster_id": cluster.id,
        },
    )


def _cluster_approve_signal(signal, user, cluster):
    """Approve validated signal for mapping from cluster detail."""
    before_data = {
        "status": signal.status,
        "approved_for_mapping": signal.approved_for_mapping,
    }

    signal.status = "approved"
    signal.approved_for_mapping = True
    signal.validated_by = user
    signal.validated_at = timezone.now()
    signal.save(update_fields=[
        "status",
        "approved_for_mapping",
        "validated_by",
        "validated_at",
        "updated_at",
    ])

    AuditLog.objects.create(
        user=user,
        action="approve_mapping",
        model_name="Signal",
        object_id=str(signal.id),
        notes=f"Signal approved for mapping from cluster detail. Cluster id={cluster.id}",
        before_data=before_data,
        after_data={
            "status": signal.status,
            "approved_for_mapping": signal.approved_for_mapping,
            "cluster_id": cluster.id,
        },
    )


def _cluster_mark_signal_noise(signal, user, cluster):
    """Mark selected signal as noise from cluster detail."""
    before_data = {
        "status": signal.status,
        "approved_for_mapping": signal.approved_for_mapping,
    }

    signal.status = "noise"
    signal.approved_for_mapping = False
    signal.validated_by = user
    signal.validated_at = timezone.now()
    signal.save(update_fields=[
        "status",
        "approved_for_mapping",
        "validated_by",
        "validated_at",
        "updated_at",
    ])

    AuditLog.objects.create(
        user=user,
        action="mark_noise",
        model_name="Signal",
        object_id=str(signal.id),
        notes=f"Signal marked as noise from cluster detail. Cluster id={cluster.id}",
        before_data=before_data,
        after_data={
            "status": signal.status,
            "approved_for_mapping": signal.approved_for_mapping,
            "cluster_id": cluster.id,
        },
    )


def _refresh_cluster_counts(cluster):
    """Refresh operational counts after selected-signal actions."""
    signals = cluster.signals.all()
    cluster.signal_count = signals.count()
    cluster.raw_count = signals.filter(status="raw").count()
    cluster.validated_count = signals.filter(status="validated").count()
    cluster.verified_count = signals.filter(status="approved").count()
    cluster.noise_count = signals.filter(status="noise").count()
    cluster.save(update_fields=[
        "signal_count",
        "raw_count",
        "validated_count",
        "verified_count",
        "noise_count",
        "updated_at",
    ])



def _safe_assessment_value(assessment, key):
    """Read value from assessment dict/object safely."""
    if not assessment:
        return ""
    if isinstance(assessment, dict):
        return assessment.get(key) or ""
    return getattr(assessment, key, "") or ""


def _short_join(items, limit=5, fallback="Belum cukup data dari signal dalam cluster."):
    cleaned = []
    for item in items:
        item = str(item or "").strip()
        if item and item not in cleaned:
            cleaned.append(item)
    if not cleaned:
        return fallback
    return "; ".join(cleaned[:limit])


def build_cluster_assessment_snapshot(cluster, signals):
    """
    Build a cluster-level 5W+1H snapshot from existing signal-level assessments.
    This does not write to DB and does not replace Signal 5W+1H.
    """
    total = len(signals)
    assessed = 0
    partial_or_fallback = 0

    what_items = []
    where_items = []
    when_items = []
    who_items = []
    why_items = []
    how_items = []
    source_items = []

    for signal in signals:
        assessment = getattr(signal, "assessment_5w1h", None)
        summary = (getattr(signal, "assessment_summary", "") or "").strip()
        quality = _safe_assessment_value(assessment, "assessment_quality")

        if summary or assessment:
            assessed += 1

        if quality in ["low_fallback", "article_based_partial"] or getattr(signal, "assessment_status", "") in ["fallback", "failed"]:
            partial_or_fallback += 1

        what_items.append(_safe_assessment_value(assessment, "what") or summary or getattr(signal, "title", ""))
        who_items.append(_safe_assessment_value(assessment, "who"))
        why_items.append(_safe_assessment_value(assessment, "why"))
        how_items.append(_safe_assessment_value(assessment, "how"))

        when_value = _safe_assessment_value(assessment, "when")
        if not when_value and getattr(signal, "published_at", None):
            when_value = signal.published_at.strftime("%Y-%m-%d %H:%M")
        when_items.append(when_value)

        loc_name = ""
        primary_locations = getattr(signal, "primary_locations", []) or []
        if primary_locations:
            loc = getattr(primary_locations[0], "location", None)
            if loc:
                loc_name = getattr(loc, "display_name", None) or getattr(loc, "name", "") or ""
                if getattr(loc, "parent", None):
                    parent_name = getattr(loc.parent, "display_name", None) or getattr(loc.parent, "name", "") or ""
                    if parent_name and parent_name != loc_name:
                        loc_name = f"{loc_name}, {parent_name}"
        if not loc_name:
            loc_name = getattr(signal, "raw_location_text", "") or getattr(signal, "admin_kabkota", "") or getattr(signal, "admin_province", "") or ""
        where_items.append(_safe_assessment_value(assessment, "where") or loc_name)

        if getattr(signal, "source", None):
            source_items.append(signal.source.name)

    if total == 0:
        quality = "empty"
    elif assessed == total:
        quality = "complete"
    elif assessed > 0:
        quality = "partial"
    else:
        quality = "metadata_based"

    period = "-"
    if cluster.date_start and cluster.date_end:
        period = f"{cluster.date_start} s.d. {cluster.date_end}"
    elif cluster.date_start:
        period = str(cluster.date_start)

    summary = (
        f"Cluster ini menghimpun {total} signal terkait {cluster.disease_tag or 'penyakit belum jelas'} "
        f"di {cluster.location_name or 'lokasi belum jelas'} pada periode {period}. "
        f"Sebanyak {assessed} signal sudah memiliki assessment 5W+1H."
    )

    if partial_or_fallback:
        summary += f" Terdapat {partial_or_fallback} assessment yang masih partial/fallback sehingga perlu verifikasi tambahan."

    return {
        "quality": quality,
        "total": total,
        "assessed": assessed,
        "pending": max(total - assessed, 0),
        "partial_or_fallback": partial_or_fallback,
        "summary": summary,
        "what": _short_join(what_items, limit=4),
        "where": _short_join(where_items, limit=6),
        "when": period if period != "-" else _short_join(when_items, limit=4),
        "who": _short_join(who_items, limit=4),
        "why": _short_join(why_items, limit=4),
        "how": _short_join(how_items, limit=4),
        "sources": _short_join(source_items, limit=6, fallback="Sumber belum teridentifikasi."),
    }

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def cluster_detail(request, pk):
    cluster = get_object_or_404(SignalCluster, pk=pk)

    primary_locations = SignalLocation.objects.filter(is_primary=True).select_related(
        "location",
        "location__parent",
    )

    signals_qs = (
        cluster.signals.select_related("source", "validated_by")
        .prefetch_related(
            Prefetch(
                "locations",
                queryset=primary_locations,
                to_attr="primary_locations",
            )
        )
        .order_by("-threat_score", "-published_at", "-created_at", "-id")
    )

    signals = list(signals_qs)
    cluster_signal_ids = {signal.id for signal in signals}

    duplicate_total = 0
    duplicate_outside_cluster_total = 0
    signals_with_duplicates = 0

    for signal in signals:
        signal.triage_flag = get_signal_triage_flag(signal)

        # Duplicate count can be broader than the current cluster because duplicate review
        # searches candidates within a review window and not only this cluster_key.
        duplicate_candidates = get_duplicate_candidates(signal, days=7, limit=30)
        signal.duplicate_count = len(duplicate_candidates)
        signal.has_duplicate_warning = signal.duplicate_count > 0
        signal.duplicate_in_cluster_count = sum(1 for item in duplicate_candidates if item.id in cluster_signal_ids)
        signal.duplicate_outside_cluster_count = max(signal.duplicate_count - signal.duplicate_in_cluster_count, 0)

        duplicate_total += signal.duplicate_count
        duplicate_outside_cluster_total += signal.duplicate_outside_cluster_count
        if signal.duplicate_count:
            signals_with_duplicates += 1

    cluster_assessment = build_cluster_assessment_snapshot(cluster, signals)
    duplicate_context = {
        "duplicate_total": duplicate_total,
        "duplicate_outside_cluster_total": duplicate_outside_cluster_total,
        "signals_with_duplicates": signals_with_duplicates,
    }

    return render(request, "intel/cluster_detail.html", {
        "page_title": "Cluster Detail",
        "cluster": cluster,
        "signals": signals,
        "cluster_assessment": cluster_assessment,
        "duplicate_context": duplicate_context,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def cluster_signal_action(request, pk):
    cluster = get_object_or_404(SignalCluster, pk=pk)

    if request.method != "POST":
        return redirect("intel:cluster_detail", pk=cluster.id)

    action = (request.POST.get("action") or "").strip()
    selected_ids = []
    for item in request.POST.getlist("selected_ids"):
        item = str(item).strip()
        if item.isdigit() and item not in selected_ids:
            selected_ids.append(item)

    if action == "mark_cluster_reviewed":
        before_data = {"status": cluster.status}
        cluster.status = "reviewed"
        cluster.reviewed_at = timezone.now()
        cluster.save(update_fields=["status", "reviewed_at", "updated_at"])
        AuditLog.objects.create(
            user=request.user,
            action="manual_edit",
            model_name="SignalCluster",
            object_id=str(cluster.id),
            notes="Cluster marked as reviewed from cluster detail.",
            before_data=before_data,
            after_data={"status": cluster.status},
        )
        messages.success(request, "Cluster ditandai sebagai reviewed.")
        return redirect("intel:cluster_detail", pk=cluster.id)

    if action == "validate_all_raw":
        target_qs = cluster.signals.filter(status="raw")
    elif action == "approve_all_validated":
        target_qs = cluster.signals.filter(status="validated")
    else:
        if not selected_ids:
            messages.warning(request, "Pilih minimal satu signal terlebih dahulu.")
            return redirect("intel:cluster_detail", pk=cluster.id)
        target_qs = cluster.signals.filter(id__in=selected_ids)

    changed_count = 0
    skipped_count = 0

    for signal in target_qs:
        if action in ["validate_selected", "validate_all_raw"]:
            if signal.status != "raw":
                skipped_count += 1
                continue
            _cluster_validate_signal(signal, request.user, cluster)
            changed_count += 1

        elif action in ["approve_selected", "approve_all_validated"]:
            if signal.status != "validated":
                skipped_count += 1
                continue
            _cluster_approve_signal(signal, request.user, cluster)
            changed_count += 1

        elif action == "noise_selected":
            # Pengaman: jangan noisekan signal yang sudah approved lewat cluster batch.
            # Jika perlu membatalkan approved signal, lakukan dari workflow terpisah/manual.
            if signal.status not in ["raw", "validated"]:
                skipped_count += 1
                continue
            _cluster_mark_signal_noise(signal, request.user, cluster)
            changed_count += 1

        else:
            messages.warning(request, "Aksi cluster tidak dikenal.")
            return redirect("intel:cluster_detail", pk=cluster.id)

    _refresh_cluster_counts(cluster)

    if skipped_count:
        messages.warning(
            request,
            f"Aksi selesai: {changed_count} signal diproses, {skipped_count} signal dilewati karena status tidak sesuai."
        )
    else:
        messages.success(request, f"Aksi selesai: {changed_count} signal diproses.")

    return redirect("intel:cluster_detail", pk=cluster.id)


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def verified_signals_list(request):
    search = request.GET.get("search", "").strip()
    disease = request.GET.get("disease", "").strip()
    geocode_status = request.GET.get("geocode_status", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    sort = request.GET.get("sort", "-published_at").strip()
    province_id = request.GET.get("province", "").strip()
    city_id = request.GET.get("city", "").strip()

    primary_locations = SignalLocation.objects.filter(is_primary=True).select_related(
        "location", "location__parent"
    )

    qs = (
        Signal.objects.select_related("source")
        .prefetch_related(
            Prefetch(
                "locations",
                queryset=primary_locations,
                to_attr="primary_locations",
            )
        )
        .filter(status="approved")
    )

    if search:
        qs = qs.filter(
            Q(title__icontains=search)
            | Q(raw_location_text__icontains=search)
            | Q(source_url__icontains=search)
            | Q(content__icontains=search)
            | Q(source__name__icontains=search)
            | Q(locations__raw_location_text__icontains=search)
            | Q(locations__location__display_name__icontains=search)
            | Q(locations__location__name__icontains=search)
            | Q(locations__location__parent__display_name__icontains=search)
        )

    if disease:
        qs = qs.filter(disease_tag__iexact=disease)

    if geocode_status:
        qs = qs.filter(geocode_status__iexact=geocode_status)

    if date_from:
        qs = qs.filter(published_at__date__gte=date_from)

    if date_to:
        qs = qs.filter(published_at__date__lte=date_to)

    if province_id:
        qs = qs.filter(
            Q(locations__is_primary=True, locations__location__parent_id=province_id)
            | Q(
                locations__is_primary=True,
                locations__location_id=province_id,
                locations__location__level="province",
            )
        )

    if city_id:
        qs = qs.filter(
            locations__is_primary=True,
            locations__location_id=city_id,
        )

    allowed_sort_fields = {
        "published_at": "published_at",
        "-published_at": "-published_at",
        "threat_score": "threat_score",
        "-threat_score": "-threat_score",
        "created_at": "created_at",
        "-created_at": "-created_at",
        "title": "title",
        "-title": "-title",
    }

    sort_field = allowed_sort_fields.get(sort, "-published_at")
    qs = qs.distinct().order_by(sort_field, "-created_at", "-id")

    stats = {
        "total_signals": qs.count(),
        "mapped_signals": qs.filter(locations__is_primary=True).distinct().count(),
        "high_risk_signals": qs.filter(is_high_risk=True).count(),
    }

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    for signal in page_obj.object_list:
        signal.triage_flag = get_signal_triage_flag(signal)

    disease_choices = (
        Signal.objects.exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .values_list("disease_tag", flat=True)
        .distinct()
        .order_by("disease_tag")
    )

    geocode_choices = (
        Signal.objects.exclude(geocode_status="")
        .exclude(geocode_status__isnull=True)
        .values_list("geocode_status", flat=True)
        .distinct()
        .order_by("geocode_status")
    )

    provinces = Location.objects.filter(
        level="province",
        is_active=True,
        is_false_positive=False,
    ).order_by("display_name", "name")

    cities = Location.objects.filter(
        level__in=["city", "regency"],
        is_active=True,
        is_false_positive=False,
    ).select_related("parent").order_by("display_name", "name")

    if province_id:
        cities = cities.filter(parent_id=province_id)

    return render(request, "intel/verified_signals_list.html", {
        "page_title": "Verified Signals",
        "page_obj": page_obj,
        "search": search,
        "date_from": date_from,
        "date_to": date_to,
        "disease": disease,
        "geocode_status": geocode_status,
        "sort": sort,
        "province_id": province_id,
        "city_id": city_id,
        "disease_choices": disease_choices,
        "geocode_choices": geocode_choices,
        "provinces": provinces,
        "cities": cities,
        "stats": stats,
    })

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def signal_generate_assessment(request, pk):
    signal = get_object_or_404(
        Signal.objects.select_related("source").prefetch_related(
            Prefetch(
                "locations",
                queryset=SignalLocation.objects.filter(is_primary=True).select_related(
                    "location",
                    "location__parent",
                ),
                to_attr="primary_locations",
            )
        ),
        pk=pk,
    )

    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER", "intel:raw_signals"))

    before_data = {
        "assessment_status": getattr(signal, "assessment_status", ""),
        "assessment_summary": getattr(signal, "assessment_summary", ""),
    }

    try:
        result = build_assessment(signal)

        signal.assessment_status = result["status"]
        signal.assessment_summary = result["summary"]
        signal.assessment_5w1h = result["assessment"]
        signal.assessment_source_text = result["source_text"][:12000]
        signal.assessment_error = result["error"]
        signal.assessment_generated_at = timezone.now()
        signal.validated_by = request.user
        signal.validated_at = timezone.now()

        signal.save(update_fields=[
            "assessment_status",
            "assessment_summary",
            "assessment_5w1h",
            "assessment_source_text",
            "assessment_error",
            "assessment_generated_at",
            "validated_by",
            "validated_at",
            "updated_at",
        ])

        AuditLog.objects.create(
            user=request.user,
            action="manual_edit",
            model_name="Signal",
            object_id=str(signal.id),
            notes="Assessment 5W+1H generated from article source",
            before_data=before_data,
            after_data={
                "assessment_status": signal.assessment_status,
                "assessment_generated_at": signal.assessment_generated_at.isoformat(),
            },
        )

        if result["error"]:
            messages.warning(
                request,
                f'Assessment dibuat dari data fallback karena artikel gagal dibuka: {result["error"]}'
            )
        else:
            if result["status"] == "ok":
                messages.success(
                    request,
                    f'Assessment article-based untuk signal "{signal.title[:60]}" berhasil dibuat.'
                )
            elif result["status"] == "fallback":
                messages.warning(
                    request,
                    f'Assessment dibuat, tetapi masih fallback karena artikel asli belum berhasil diproses untuk signal "{signal.title[:60]}".'
                )
            else:
                messages.error(
                    request,
                    f'Assessment gagal dibuat untuk signal "{signal.title[:60]}".'
                )

    except Exception as exc:
        signal.assessment_status = "failed"
        signal.assessment_error = str(exc)
        signal.assessment_generated_at = timezone.now()
        signal.save(update_fields=[
            "assessment_status",
            "assessment_error",
            "assessment_generated_at",
            "updated_at",
        ])

        messages.error(request, f"Gagal membuat assessment: {exc}")

    return redirect(request.META.get("HTTP_REFERER", "intel:raw_signals"))

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def signal_update_resolved_url(request, pk):
    signal = get_object_or_404(Signal, pk=pk)

    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER", "intel:raw_signals"))

    resolved_url = (request.POST.get("resolved_url") or "").strip()
    run_assessment = request.POST.get("run_assessment") == "1"

    if not resolved_url:
        messages.error(request, "URL artikel asli tidak boleh kosong.")
        return redirect(request.META.get("HTTP_REFERER", "intel:raw_signals"))

    validator = URLValidator()

    try:
        validator(resolved_url)
    except ValidationError:
        messages.error(request, "Format URL tidak valid.")
        return redirect(request.META.get("HTTP_REFERER", "intel:raw_signals"))

    before_data = {
        "resolved_url": signal.resolved_url,
        "url_resolution_status": signal.url_resolution_status,
        "url_resolution_method": signal.url_resolution_method,
        "assessment_status": getattr(signal, "assessment_status", ""),
    }

    signal.resolved_url = resolved_url
    signal.url_resolution_status = "manual"
    signal.url_resolution_method = "manual_validator"
    signal.url_resolution_error = ""
    signal.validated_by = request.user
    signal.validated_at = timezone.now()

    signal.save(update_fields=[
        "resolved_url",
        "url_resolution_status",
        "url_resolution_method",
        "url_resolution_error",
        "validated_by",
        "validated_at",
        "updated_at",
    ])

    if run_assessment:
        try:
            result = build_assessment(signal)

            signal.assessment_status = result["status"]
            signal.assessment_summary = result["summary"]
            signal.assessment_5w1h = result["assessment"]
            signal.assessment_source_text = result["source_text"][:15000]
            signal.assessment_error = result["error"]
            signal.assessment_generated_at = timezone.now()

            signal.save(update_fields=[
                "assessment_status",
                "assessment_summary",
                "assessment_5w1h",
                "assessment_source_text",
                "assessment_error",
                "assessment_generated_at",
                "updated_at",
            ])

            messages.success(
                request,
                f'URL artikel asli disimpan dan assessment ulang berhasil untuk signal "{signal.title[:60]}".'
            )

        except Exception as exc:
            signal.assessment_status = "failed"
            signal.assessment_error = str(exc)
            signal.assessment_generated_at = timezone.now()
            signal.save(update_fields=[
                "assessment_status",
                "assessment_error",
                "assessment_generated_at",
                "updated_at",
            ])

            messages.error(request, f"URL tersimpan, tetapi re-assessment gagal: {exc}")

    else:
        messages.success(request, "URL artikel asli berhasil disimpan.")

    AuditLog.objects.create(
        user=request.user,
        action="manual_edit",
        model_name="Signal",
        object_id=str(signal.id),
        notes="Manual resolved URL update",
        before_data=before_data,
        after_data={
            "resolved_url": signal.resolved_url,
            "url_resolution_status": signal.url_resolution_status,
            "url_resolution_method": signal.url_resolution_method,
            "assessment_status": getattr(signal, "assessment_status", ""),
        },
    )

    return redirect(request.META.get("HTTP_REFERER", "intel:raw_signals"))
    
def signal_region_summary(request):
    disease = (request.GET.get("disease") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    level = (request.GET.get("level") or "province").strip()

    qs = SignalLocation.objects.filter(
        is_primary=True,
        location__isnull=False,
        signal__status__in=["raw", "validated", "approved"],
    ).select_related("location", "location__parent", "signal")

    if disease:
        qs = qs.filter(signal__disease_tag__iexact=disease)

    if date_from:
        qs = qs.filter(signal__published_at__date__gte=date_from)

    if date_to:
        qs = qs.filter(signal__published_at__date__lte=date_to)

    if level == "province":
        summary = (
            qs.values(
                "location__parent_id",
                "location__parent__display_name",
                "location__parent__name",
                "location_id",
                "location__display_name",
                "location__name",
                "location__level",
            )
            .annotate(
                total_signals=Count("signal_id", distinct=True),
                avg_score=Avg("signal__threat_score"),
                high_risk_count=Count("signal_id", filter=Q(signal__is_high_risk=True), distinct=True),
            )
            .order_by("-total_signals")
        )

        normalized_rows = []
        for row in summary:
            if row["location__level"] == "province":
                region_name = row["location__display_name"] or row["location__name"]
            else:
                region_name = row["location__parent__display_name"] or row["location__parent__name"]

            normalized_rows.append({
                "region_name": region_name,
                "total_signals": row["total_signals"],
                "avg_score": row["avg_score"] or 0,
                "high_risk_count": row["high_risk_count"],
            })

        # gabungkan kalau ada beberapa rows untuk province yang sama
        merged = {}
        for row in normalized_rows:
            key = row["region_name"]
            if key not in merged:
                merged[key] = {
                    "region_name": key,
                    "total_signals": 0,
                    "score_sum": 0.0,
                    "score_count": 0,
                    "high_risk_count": 0,
                }

            merged[key]["total_signals"] += row["total_signals"]
            merged[key]["high_risk_count"] += row["high_risk_count"]
            if row["avg_score"] is not None:
                merged[key]["score_sum"] += row["avg_score"] * row["total_signals"]
                merged[key]["score_count"] += row["total_signals"]

        rows = []
        for item in merged.values():
            avg_score = item["score_sum"] / item["score_count"] if item["score_count"] else 0
            rows.append({
                "region_name": item["region_name"],
                "total_signals": item["total_signals"],
                "avg_score": round(avg_score, 2),
                "high_risk_count": item["high_risk_count"],
            })

        rows.sort(key=lambda x: (-x["total_signals"], x["region_name"]))

    else:
        summary = (
            qs.filter(location__level__in=["city", "regency"])
            .values(
                "location_id",
                "location__display_name",
                "location__name",
                "location__parent__display_name",
                "location__parent__name",
                "location__level",
            )
            .annotate(
                total_signals=Count("signal_id", distinct=True),
                avg_score=Avg("signal__threat_score"),
                high_risk_count=Count("signal_id", filter=Q(signal__is_high_risk=True), distinct=True),
            )
            .order_by("-total_signals")
        )

        rows = [
            {
                "region_name": row["location__display_name"] or row["location__name"],
                "province_name": row["location__parent__display_name"] or row["location__parent__name"] or "-",
                "level": row["location__level"],
                "total_signals": row["total_signals"],
                "avg_score": round(row["avg_score"] or 0, 2),
                "high_risk_count": row["high_risk_count"],
            }
            for row in summary
        ]

    disease_choices = (
        SignalLocation.objects.exclude(signal__disease_tag="")
        .exclude(signal__disease_tag__isnull=True)
        .values_list("signal__disease_tag", flat=True)
        .distinct()
        .order_by("signal__disease_tag")
    )

    return render(request, "intel/signal_region_summary.html", {
        "rows": rows,
        "level": level,
        "disease": disease,
        "date_from": date_from,
        "date_to": date_to,
        "disease_choices": disease_choices,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def signal_mark_validated(request, pk):
    signal = get_object_or_404(Signal, pk=pk)

    before_data = {
        "status": signal.status,
        "approved_for_mapping": signal.approved_for_mapping,
    }

    signal.status = "validated"
    signal.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        user=request.user,
        action="mark_validated",
        model_name="Signal",
        object_id=str(signal.id),
        notes="Signal marked as validated",
        before_data=before_data,
        after_data={
            "status": signal.status,
            "approved_for_mapping": signal.approved_for_mapping,
        },
    )

    messages.success(request, f'Signal "{signal.title}" ditandai sebagai validated.')
    return redirect("intel:raw_signals")

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def signal_mark_noise(request, pk):
    signal = get_object_or_404(Signal, pk=pk)

    before_data = {
        "status": signal.status,
        "approved_for_mapping": signal.approved_for_mapping,
    }

    signal.status = "noise"
    signal.approved_for_mapping = False
    signal.save(update_fields=["status", "approved_for_mapping", "updated_at"])

    AuditLog.objects.create(
        user=request.user,
        action="mark_noise",
        model_name="Signal",
        object_id=str(signal.id),
        notes="Signal marked as noise",
        before_data=before_data,
        after_data={
            "status": signal.status,
            "approved_for_mapping": signal.approved_for_mapping,
        },
    )

    messages.success(request, f'Signal "{signal.title}" ditandai sebagai noise.')
    return redirect("intel:raw_signals")


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def signal_approve_mapping(request, pk):
    signal = get_object_or_404(Signal, pk=pk)

    before_data = {
        "status": signal.status,
        "approved_for_mapping": signal.approved_for_mapping,
    }

    signal.status = "approved"
    signal.approved_for_mapping = True
    signal.save(update_fields=["status", "approved_for_mapping", "updated_at"])

    AuditLog.objects.create(
        user=request.user,
        action="approve_mapping",
        model_name="Signal",
        object_id=str(signal.id),
        notes="Signal approved for mapping",
        before_data=before_data,
        after_data={
            "status": signal.status,
            "approved_for_mapping": signal.approved_for_mapping,
        },
    )

    messages.success(request, f'Signal "{signal.title}" berhasil di-approve untuk mapping.')
    return redirect("intel:raw_signals")


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def signal_quick_score(request, pk):
    signal = get_object_or_404(Signal, pk=pk)

    if request.method == "POST":
        try:
            new_score = int(request.POST.get("threat_score", signal.threat_score or 0))
        except ValueError:
            messages.error(request, "Nilai threat score tidak valid.")
            return redirect(request.META.get("HTTP_REFERER", "intel:raw_signals"))

        if new_score < 0 or new_score > 100:
            messages.error(request, "Nilai threat score harus berada antara 0 sampai 100.")
            return redirect(request.META.get("HTTP_REFERER", "intel:raw_signals"))

        signal.threat_score = new_score
        signal.is_high_risk = new_score > 50
        signal.validated_by = request.user
        signal.validated_at = timezone.now()

        signal.save(update_fields=[
            "threat_score",
            "is_high_risk",
            "validated_by",
            "validated_at",
            "updated_at",
        ])

        messages.success(
            request,
            f'Skor signal "{signal.title[:60]}" berhasil diperbarui.'
        )

        return redirect(request.META.get("HTTP_REFERER", "intel:raw_signals"))

    return redirect("intel:raw_signals")



def _trend_status_config(status_filter):
    """
    Status filter khusus halaman Disease Trend.
    Dibuat lokal agar tidak mengganggu logic report existing.
    """
    if status_filter == "approved":
        return ["approved"], "Approved Mapping"
    if status_filter == "validated":
        return ["validated", "approved"], "Validated + Approved"
    if status_filter == "raw":
        return ["raw"], "Raw Crawling"
    return ["raw", "validated", "approved"], "Data Operasional Crawling"


def _trend_risk_label(avg_score, high_risk_total, total):
    avg_score = avg_score or 0
    high_risk_total = high_risk_total or 0
    total = total or 0

    if high_risk_total >= 3 or avg_score >= 70:
        return "Tinggi"
    if high_risk_total >= 1 or avg_score >= 50:
        return "Sedang-Tinggi"
    if avg_score >= 35 or total >= 3:
        return "Sedang"
    return "Rendah"


def _trend_label(current_total, previous_total):
    current_total = current_total or 0
    previous_total = previous_total or 0

    if previous_total <= 0 and current_total > 0:
        return "Baru/Muncul"
    if current_total > previous_total:
        return "Naik"
    if current_total < previous_total:
        return "Turun"
    return "Stabil"


def _safe_growth_percent(current_total, previous_total):
    current_total = current_total or 0
    previous_total = previous_total or 0

    if previous_total <= 0:
        return 100 if current_total > 0 else 0
    return round(((current_total - previous_total) / previous_total) * 100, 2)


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def disease_trends(request):
    """
    Disease Trend Intelligence.

    Tujuan:
    - Menampilkan tren penyakit berbasis OSINT.
    - Membandingkan periode berjalan dengan periode sebelumnya.
    - Memprioritaskan penyakit dan lokasi yang perlu dipantau analis.

    Catatan implementasi:
    - Tidak menambah model/migrasi.
    - Menggunakan Signal + SignalLocation existing.
    """
    try:
        days = int(request.GET.get("days", "7"))
    except ValueError:
        days = 7

    if days not in [7, 14, 30]:
        days = 7

    selected_disease = (request.GET.get("disease") or "").strip()
    status_filter = (request.GET.get("status_filter") or "operational").strip()
    selected_statuses, status_label = _trend_status_config(status_filter)

    date_to_raw = (request.GET.get("date_to") or "").strip()
    if date_to_raw:
        try:
            date_to_obj = timezone.datetime.fromisoformat(date_to_raw).date()
        except ValueError:
            date_to_obj = timezone.now().date()
    else:
        date_to_obj = timezone.now().date()

    current_start = date_to_obj - timedelta(days=days - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)

    current_qs = (
        Signal.objects.filter(
            published_at__date__gte=current_start,
            published_at__date__lte=date_to_obj,
            status__in=selected_statuses,
        )
        .exclude(status="noise")
        .select_related("source")
    )

    previous_qs = (
        Signal.objects.filter(
            published_at__date__gte=previous_start,
            published_at__date__lte=previous_end,
            status__in=selected_statuses,
        )
        .exclude(status="noise")
    )

    if selected_disease:
        current_qs = current_qs.filter(disease_tag__iexact=selected_disease)
        previous_qs = previous_qs.filter(disease_tag__iexact=selected_disease)

    current_total = current_qs.count()
    previous_total = previous_qs.count()
    total_growth_abs = current_total - previous_total
    total_growth_percent = _safe_growth_percent(current_total, previous_total)

    high_risk_total = current_qs.filter(threat_score__gte=70).count()
    avg_score = round(current_qs.aggregate(avg=Avg("threat_score"))["avg"] or 0, 2)
    active_disease_count = (
        current_qs.exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .values("disease_tag")
        .distinct()
        .count()
    )

    current_disease_rows = list(
        current_qs.exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .values("disease_tag")
        .annotate(
            total=Count("id", distinct=True),
            avg_score=Avg("threat_score"),
            high_risk_total=Count("id", filter=Q(threat_score__gte=70), distinct=True),
            first_signal_at=Min("published_at"),
            last_signal_at=Max("published_at"),
        )
        .order_by("-total", "-avg_score", "disease_tag")
    )

    previous_disease_map = {
        row["disease_tag"]: row["total"]
        for row in previous_qs.exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .values("disease_tag")
        .annotate(total=Count("id", distinct=True))
    }

    disease_trend_rows = []
    for row in current_disease_rows:
        disease_name = row["disease_tag"] or "-"
        total = row["total"] or 0
        prev_total = previous_disease_map.get(disease_name, 0)
        growth_abs = total - prev_total
        growth_percent = _safe_growth_percent(total, prev_total)
        row_avg = round(row["avg_score"] or 0, 2)
        row_high = row["high_risk_total"] or 0

        disease_trend_rows.append({
            "disease": disease_name,
            "total": total,
            "previous_total": prev_total,
            "growth_abs": growth_abs,
            "growth_percent": growth_percent,
            "trend_label": _trend_label(total, prev_total),
            "avg_score": row_avg,
            "high_risk_total": row_high,
            "risk_label": _trend_risk_label(row_avg, row_high, total),
            "first_signal_at": row["first_signal_at"],
            "last_signal_at": row["last_signal_at"],
        })

    rising_disease_rows = sorted(
        disease_trend_rows,
        key=lambda x: (x["growth_abs"], x["growth_percent"], x["total"], x["avg_score"]),
        reverse=True,
    )[:10]

    priority_disease_rows = sorted(
        disease_trend_rows,
        key=lambda x: (x["high_risk_total"], x["avg_score"], x["total"], x["growth_abs"]),
        reverse=True,
    )[:10]

    top_location_rows = list(
        SignalLocation.objects.filter(
            signal__in=current_qs,
            is_primary=True,
            location__isnull=False,
        )
        .select_related("location", "location__parent")
        .values(
            "location_id",
            "location__display_name",
            "location__name",
            "location__level",
            "location__parent__display_name",
            "location__parent__name",
        )
        .annotate(
            total=Count("signal_id", distinct=True),
            avg_score=Avg("signal__threat_score"),
            high_risk_total=Count("signal_id", filter=Q(signal__threat_score__gte=70), distinct=True),
        )
        .order_by("-total", "-avg_score", "location__display_name")[:15]
    )

    normalized_locations = []
    for item in top_location_rows:
        loc_name = item["location__display_name"] or item["location__name"] or "-"
        parent_name = item["location__parent__display_name"] or item["location__parent__name"] or ""
        avg_loc_score = round(item["avg_score"] or 0, 2)
        normalized_locations.append({
            "location_id": item["location_id"],
            "location_name": loc_name,
            "parent_name": parent_name,
            "level": item["location__level"] or "-",
            "total": item["total"] or 0,
            "avg_score": avg_loc_score,
            "high_risk_total": item["high_risk_total"] or 0,
            "risk_label": _trend_risk_label(avg_loc_score, item["high_risk_total"] or 0, item["total"] or 0),
        })

    top_disease_names = [item["disease"] for item in disease_trend_rows[:5]]
    daily_rows = list(
        current_qs.exclude(published_at__isnull=True)
        .exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .filter(disease_tag__in=top_disease_names if top_disease_names else [])
        .annotate(day=TruncDate("published_at"))
        .values("day", "disease_tag")
        .annotate(total=Count("id", distinct=True))
        .order_by("day", "disease_tag")
    )

    day_labels = []
    cursor = current_start
    while cursor <= date_to_obj:
        day_labels.append(cursor)
        cursor += timedelta(days=1)

    daily_map = {
        (row["day"], row["disease_tag"]): row["total"]
        for row in daily_rows
        if row["day"]
    }

    chart_series = []
    for disease_name in top_disease_names:
        chart_series.append({
            "label": disease_name,
            "data": [daily_map.get((day, disease_name), 0) for day in day_labels],
        })

    chart_data = {
        "labels": [day.strftime("%d/%m") for day in day_labels],
        "series": chart_series,
    }

    recent_priority_signals = (
        current_qs.filter(Q(threat_score__gte=70) | Q(triage_priority__in=["urgent", "high"]))
        .select_related("source", "cluster")
        .order_by("-threat_score", "-published_at", "-created_at")[:15]
    )

    disease_choices = (
        Signal.objects.exclude(status="noise")
        .exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .values_list("disease_tag", flat=True)
        .distinct()
        .order_by("disease_tag")
    )

    top_rising = rising_disease_rows[0] if rising_disease_rows else None
    top_priority = priority_disease_rows[0] if priority_disease_rows else None
    top_location = normalized_locations[0] if normalized_locations else None

    narrative_items = []
    if top_rising:
        narrative_items.append(
            f"{top_rising['disease']} menjadi penyakit dengan kenaikan paling menonjol, dari {top_rising['previous_total']} menjadi {top_rising['total']} signal pada periode ini."
        )
    if top_priority:
        narrative_items.append(
            f"{top_priority['disease']} perlu diprioritaskan karena memiliki {top_priority['high_risk_total']} signal high-risk dengan rata-rata skor {top_priority['avg_score']}."
        )
    if top_location:
        narrative_items.append(
            f"Konsentrasi lokasi tertinggi berada pada {top_location['location_name']} dengan {top_location['total']} signal dan rata-rata skor {top_location['avg_score']}."
        )
    if not narrative_items:
        narrative_items.append("Belum terdapat tren penyakit yang menonjol pada parameter yang dipilih.")

    context = {
        "page_title": "Disease Trend Intelligence",
        "days": days,
        "status_filter": status_filter,
        "status_label": status_label,
        "selected_disease": selected_disease,
        "date_to": date_to_obj.isoformat(),
        "current_start": current_start,
        "current_end": date_to_obj,
        "previous_start": previous_start,
        "previous_end": previous_end,
        "current_total": current_total,
        "previous_total": previous_total,
        "total_growth_abs": total_growth_abs,
        "total_growth_percent": total_growth_percent,
        "active_disease_count": active_disease_count,
        "high_risk_total": high_risk_total,
        "avg_score": avg_score,
        "disease_trend_rows": disease_trend_rows[:20],
        "rising_disease_rows": rising_disease_rows,
        "priority_disease_rows": priority_disease_rows,
        "top_location_rows": normalized_locations,
        "recent_priority_signals": recent_priority_signals,
        "disease_choices": disease_choices,
        "chart_data_json": json.dumps(chart_data, default=str),
        "narrative_items": narrative_items,
    }

    return render(request, "intel/disease_trends.html", context)

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def dashboard_overview(request):
    now = timezone.now()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)

    all_signals = Signal.objects.all()
    recent_signals = Signal.objects.filter(published_at__gte=last_7d)

    total_24h = Signal.objects.filter(published_at__gte=last_24h).count()
    total_week = recent_signals.count()

    total_all = all_signals.count()
    geocode_ok_count = Signal.objects.filter(geocode_status="OK").count()
    high_risk_count = Signal.objects.filter(threat_score__gt=50).count()

    geocode_success_rate = round((geocode_ok_count / total_all) * 100, 2) if total_all else 0
    high_risk_rate = round((high_risk_count / total_all) * 100, 2) if total_all else 0

    top_diseases = (
        recent_signals.exclude(disease_tag="")
        .values("disease_tag")
        .annotate(total=Count("id"))
        .order_by("-total", "disease_tag")[:5]
    )

    top_locations = (
        SignalLocation.objects.filter(
            signal__published_at__gte=last_7d,
            is_primary=True,
            location__isnull=False,
        )
        .values("location__display_name")
        .annotate(total=Count("id"))
        .order_by("-total", "location__display_name")[:5]
    )

    recent_high_risk = (
        Signal.objects.filter(threat_score__gt=50)
        .select_related("source")
        .order_by("-published_at", "-created_at")[:10]
    )

    recent_errors = (
        Signal.objects.exclude(geocode_status="OK")
        .exclude(geocode_status="PENDING")
        .order_by("-published_at", "-created_at")[:10]
    )

    context = {
        "page_title": "Dashboard Overview",
        "total_24h": total_24h,
        "total_week": total_week,
        "geocode_success_rate": geocode_success_rate,
        "high_risk_rate": high_risk_rate,
        "top_diseases": top_diseases,
        "top_locations": top_locations,
        "recent_high_risk": recent_high_risk,
        "recent_errors": recent_errors,
    }
    return render(request, "intel/dashboard_overview.html", context)

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def geocode_error_center(request):
    error_statuses = [
        "empty_loc",
        "not_found",
        "net_err",
        "skip_noise",
        "skip_too_general",
        "skip_low_conf",
        "pending",
        "EMPTY_LOC",
        "NOT_FOUND",
        "NET_ERR",
        "SKIP_NOISE",
        "SKIP_TOO_GENERAL",
        "SKIP_LOW_CONF",
        "PENDING",
    ]

    primary_locations = SignalLocation.objects.filter(is_primary=True).select_related(
        "location",
        "location__parent",
    )

    qs = (
        Signal.objects.select_related("source", "validated_by")
        .prefetch_related(
            Prefetch(
                "locations",
                queryset=primary_locations,
                to_attr="primary_locations",
            )
        )
        .filter(
            Q(geocode_status__in=error_statuses)
            | Q(locations__isnull=True)
        )
        .distinct()
    )

    q = request.GET.get("q", "").strip()
    geocode_status = request.GET.get("geocode_status", "").strip()
    disease = request.GET.get("disease", "").strip()
    sort = request.GET.get("sort", "-published_at").strip()

    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(raw_location_text__icontains=q)
            | Q(admin_province__icontains=q)
            | Q(admin_kabkota__icontains=q)
            | Q(source_url__icontains=q)
            | Q(content__icontains=q)
            | Q(source__name__icontains=q)
            | Q(locations__raw_location_text__icontains=q)
            | Q(locations__location__display_name__icontains=q)
            | Q(locations__location__name__icontains=q)
            | Q(locations__location__parent__display_name__icontains=q)
        )

    if geocode_status:
        qs = qs.filter(geocode_status__iexact=geocode_status)

    if disease:
        qs = qs.filter(disease_tag__iexact=disease)

    allowed_sort = {
        "published_at": "published_at",
        "-published_at": "-published_at",
        "threat_score": "threat_score",
        "-threat_score": "-threat_score",
        "title": "title",
        "-title": "-title",
        "created_at": "created_at",
        "-created_at": "-created_at",
    }

    qs = qs.distinct().order_by(allowed_sort.get(sort, "-published_at"), "-created_at", "-id")

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    for signal in page_obj.object_list:
        signal.triage_flag = get_signal_triage_flag(signal)

        duplicate_count = 0

        if signal.disease_tag and signal.raw_location_text:
            duplicate_qs = (
                Signal.objects.filter(
                    disease_tag__iexact=signal.disease_tag,
                    raw_location_text__iexact=signal.raw_location_text,
                )
                .exclude(id=signal.id)
                .exclude(status="noise")
            )

            if signal.published_at:
                duplicate_qs = duplicate_qs.filter(
                    published_at__range=(
                        signal.published_at - timedelta(days=3),
                        signal.published_at + timedelta(days=3),
                    )
                )

            duplicate_count = duplicate_qs.count()

        signal.duplicate_count = duplicate_count
        signal.has_duplicate_warning = duplicate_count > 0

    geocode_choices = (
        Signal.objects.exclude(geocode_status="")
        .exclude(geocode_status__isnull=True)
        .filter(
            Q(geocode_status__in=error_statuses)
            | Q(locations__isnull=True)
        )
        .values_list("geocode_status", flat=True)
        .distinct()
        .order_by("geocode_status")
    )

    disease_choices = (
        Signal.objects.exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .values_list("disease_tag", flat=True)
        .distinct()
        .order_by("disease_tag")
    )

    context = {
        "page_title": "Geocode Error Center",
        "page_obj": page_obj,
        "q": q,
        "geocode_status": geocode_status,
        "disease": disease,
        "sort": sort,
        "geocode_choices": geocode_choices,
        "disease_choices": disease_choices,
    }

    return render(request, "intel/geocode_error_center.html", context)



def get_safe_next_url(request, default_name="intel:raw_signals"):
    """
    Return redirect tujuan yang aman untuk workflow edit lokasi.
    Prioritas: POST next -> GET next -> default route.
    """
    default_url = reverse(default_name)

    next_url = (
        request.POST.get("next")
        or request.GET.get("next")
        or ""
    ).strip()

    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url

    return default_url

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def geocode_manual_update(request, pk):
    signal = get_object_or_404(Signal, pk=pk)
    next_url = get_safe_next_url(request)

    signal_location = (
        SignalLocation.objects.filter(signal=signal, is_primary=True)
        .select_related("location")
        .first()
    )

    current_location = (
        signal_location.location
        if signal_location and signal_location.location
        else None
    )

    initial_province = None
    initial_kabkota = None

    if current_location:
        if current_location.level == "province":
            initial_province = current_location

        elif current_location.level in ["city", "regency"]:
            initial_kabkota = current_location

            if current_location.province_code:
                initial_province = Location.objects.filter(
                    level="province",
                    province_code=current_location.province_code,
                ).first()

            elif current_location.parent and current_location.parent.level == "province":
                initial_province = current_location.parent

    initial_data = {
        "raw_location_text": signal.raw_location_text,
        "geocode_status": signal.geocode_status,
        "province": initial_province,
        "kabkota": initial_kabkota,
        "confidence": signal_location.confidence if signal_location else None,
        "notes": signal.validation_notes,
    }

    if request.method == "POST":
        province_obj = None
        province_id = request.POST.get("province")

        if province_id:
            province_obj = Location.objects.filter(
                id=province_id,
                level="province",
            ).first()

        province_code = province_obj.province_code if province_obj else None
        form = GeocodeManualUpdateForm(request.POST, province_code=province_code)

        if form.is_valid():
            old_status = signal.geocode_status
            old_raw_location = signal.raw_location_text
            old_location_id = signal_location.location_id if signal_location else None

            province = form.cleaned_data["province"]
            kabkota = form.cleaned_data["kabkota"]
            confidence = form.cleaned_data["confidence"]
            final_location = kabkota or province

            signal.raw_location_text = form.cleaned_data["raw_location_text"]
            signal.geocode_status = (form.cleaned_data["geocode_status"] or "manual").lower()
            signal.validation_notes = form.cleaned_data["notes"]
            signal.validated_by = request.user
            signal.validated_at = timezone.now()

            # Sinkronisasi field admin legacy agar tampilan Raw tidak kosong
            if kabkota:
                signal.admin_kabkota = kabkota.display_name or kabkota.name

                if kabkota.parent:
                    signal.admin_province = kabkota.parent.display_name or kabkota.parent.name
                elif province:
                    signal.admin_province = province.display_name or province.name
                else:
                    signal.admin_province = ""

                signal.location_level = kabkota.level

            elif province:
                signal.admin_province = province.display_name or province.name
                signal.admin_kabkota = ""
                signal.location_level = province.level

            else:
                signal.admin_province = ""
                signal.admin_kabkota = ""
                signal.location_level = ""

            if final_location:
                signal.approved_for_mapping = True

            signal.save(update_fields=[
                "raw_location_text",
                "geocode_status",
                "admin_province",
                "admin_kabkota",
                "location_level",
                "validation_notes",
                "validated_by",
                "validated_at",
                "approved_for_mapping",
                "updated_at",
            ])

            if final_location:
                if signal_location:
                    signal_location.location = final_location
                    signal_location.raw_location_text = signal.raw_location_text
                    signal_location.confidence = confidence
                    signal_location.method = "manual"
                    signal_location.is_primary = True
                    signal_location.save(update_fields=[
                        "location",
                        "raw_location_text",
                        "confidence",
                        "method",
                        "is_primary",
                        "updated_at",
                    ])
                else:
                    SignalLocation.objects.create(
                        signal=signal,
                        location=final_location,
                        raw_location_text=signal.raw_location_text,
                        confidence=confidence,
                        method="manual",
                        is_primary=True,
                    )

            AuditLog.objects.create(
                user=request.user,
                action="manual_edit",
                model_name="Signal",
                object_id=str(signal.id),
                notes=f"Manual geocode update from {old_status} to {signal.geocode_status}",
                before_data={
                    "geocode_status": old_status,
                    "raw_location_text": old_raw_location,
                    "location_id": old_location_id,
                },
                after_data={
                    "geocode_status": signal.geocode_status,
                    "raw_location_text": signal.raw_location_text,
                    "province_id": province.id if province else None,
                    "kabkota_id": kabkota.id if kabkota else None,
                    "location_id": final_location.id if final_location else None,
                    "confidence": confidence,
                    "approved_for_mapping": signal.approved_for_mapping,
                },
            )

            messages.success(
                request,
                f'Geocode signal "{signal.title[:60]}" berhasil diperbarui secara manual.'
            )

            return redirect(next_url)

    else:
        province_code = initial_province.province_code if initial_province else None
        form = GeocodeManualUpdateForm(initial=initial_data, province_code=province_code)

    context = {
        "page_title": "Manual Geocode Update",
        "signal": signal,
        "form": form,
        "signal_location": signal_location,
        "next_url": next_url,
    }

    return render(request, "intel/geocode_manual_update.html", context)

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def geocode_mark_manual_ok(request, pk):
    signal = get_object_or_404(Signal, pk=pk)

    before_data = {
        "geocode_status": signal.geocode_status,
        "approved_for_mapping": signal.approved_for_mapping,
    }

    signal.geocode_status = "manual"
    signal.approved_for_mapping = True
    signal.validated_by = request.user
    signal.validated_at = timezone.now()
    signal.save(update_fields=[
        "geocode_status",
        "approved_for_mapping",
        "validated_by",
        "validated_at",
        "updated_at",
    ])

    AuditLog.objects.create(
        user=request.user,
        action="manual_edit",
        model_name="Signal",
        object_id=str(signal.id),
        notes="Geocode status set to manual",
        before_data=before_data,
        after_data={
            "geocode_status": signal.geocode_status,
            "approved_for_mapping": signal.approved_for_mapping,
        },
    )

    messages.success(request, f'Signal "{signal.title[:60]}" ditandai sebagai geocode manual.')
    return redirect(request.META.get("HTTP_REFERER", "intel:geocode_error_center"))

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def gazetteer_location_create(request):
    if request.method == "POST":
        form = LocationForm(request.POST)
        if form.is_valid():
            loc = form.save()

            AuditLog.objects.create(
                user=request.user,
                action="create",
                model_name="Location",
                object_id=str(loc.id),
                notes="Location created from Gazetteer Manager",
                after_data={
                    "name": loc.name,
                    "display_name": loc.display_name,
                    "level": loc.level,
                },
            )

            messages.success(request, f'Location "{loc.display_name or loc.name}" berhasil ditambahkan.')
            return redirect("intel:gazetteer_manager")
    else:
        form = LocationForm()

    return render(request, "intel/gazetteer_location_form.html", {
        "page_title": "Tambah Location",
        "form": form,
        "mode": "create",
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def gazetteer_location_edit(request, pk):
    loc = get_object_or_404(Location, pk=pk)

    if request.method == "POST":
        before_data = {
            "name": loc.name,
            "display_name": loc.display_name,
            "level": loc.level,
            "is_active": loc.is_active,
            "is_false_positive": loc.is_false_positive,
        }

        form = LocationForm(request.POST, instance=loc)
        if form.is_valid():
            loc = form.save()

            AuditLog.objects.create(
                user=request.user,
                action="update",
                model_name="Location",
                object_id=str(loc.id),
                notes="Location updated from Gazetteer Manager",
                before_data=before_data,
                after_data={
                    "name": loc.name,
                    "display_name": loc.display_name,
                    "level": loc.level,
                    "is_active": loc.is_active,
                    "is_false_positive": loc.is_false_positive,
                },
            )

            messages.success(request, f'Location "{loc.display_name or loc.name}" berhasil diperbarui.')
            return redirect("intel:gazetteer_manager")
    else:
        form = LocationForm(instance=loc)

    return render(request, "intel/gazetteer_location_form.html", {
        "page_title": "Edit Location",
        "form": form,
        "mode": "edit",
        "location_obj": loc,
    })

# @role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
# def gazetteer_alias_manager(request):
#     qs = LocationAlias.objects.select_related("location").all()

#     q = request.GET.get("q", "").strip()
#     is_active = request.GET.get("is_active", "").strip()
#     is_primary = request.GET.get("is_primary", "").strip()

#     if q:
#         qs = qs.filter(
#             Q(alias__icontains=q) |
#             Q(normalized_alias__icontains=q) |
#             Q(location__display_name__icontains=q) |
#             Q(location__name__icontains=q)
#         )

#     if is_active == "yes":
#         qs = qs.filter(is_active=True)
#     elif is_active == "no":
#         qs = qs.filter(is_active=False)

#     if is_primary == "yes":
#         qs = qs.filter(is_primary=True)
#     elif is_primary == "no":
#         qs = qs.filter(is_primary=False)

#     qs = qs.order_by("alias")

#     paginator = Paginator(qs, 25)
#     page_number = request.GET.get("page")
#     page_obj = paginator.get_page(page_number)
#     for signal in page_obj.object_list:
#         signal.triage_flag = get_signal_triage_flag(signal)

#         duplicate_count = 0

#         if signal.disease_tag and signal.raw_location_text:
#             duplicate_qs = (
#                 Signal.objects.filter(
#                     disease_tag__iexact=signal.disease_tag,
#                     raw_location_text__iexact=signal.raw_location_text,
#                 )
#                 .exclude(id=signal.id)
#                 .exclude(status="noise")
#             )

#             if signal.published_at:
#                 duplicate_qs = duplicate_qs.filter(
#                     published_at__range=(
#                         signal.published_at - timedelta(days=3),
#                         signal.published_at + timedelta(days=3),
#                     )
#                 )

#             duplicate_count = duplicate_qs.count()

#         signal.duplicate_count = duplicate_count
#         signal.has_duplicate_warning = duplicate_count > 0

#     return render(request, "intel/gazetteer_alias_manager.html", {
#         "page_title": "Location Alias Manager",
#         "page_obj": page_obj,
#         "q": q,
#         "is_active": is_active,
#         "is_primary": is_primary,
#     })

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def gazetteer_alias_manager(request):
    qs = LocationAlias.objects.select_related("location").all()
    q = request.GET.get("q", "").strip()
    is_active = request.GET.get("is_active", "").strip()
    is_primary = request.GET.get("is_primary", "").strip()

    if q:
        qs = qs.filter(
            Q(alias__icontains=q)
            | Q(normalized_alias__icontains=q)
            | Q(location__display_name__icontains=q)
            | Q(location__name__icontains=q)
        )

    if is_active == "yes":
        qs = qs.filter(is_active=True)
    elif is_active == "no":
        qs = qs.filter(is_active=False)

    if is_primary == "yes":
        qs = qs.filter(is_primary=True)
    elif is_primary == "no":
        qs = qs.filter(is_primary=False)

    qs = qs.order_by("alias")

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "intel/gazetteer_alias_manager.html", {
        "page_title": "Location Alias Manager",
        "page_obj": page_obj,
        "q": q,
        "is_active": is_active,
        "is_primary": is_primary,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def gazetteer_alias_create(request):
    if request.method == "POST":
        form = LocationAliasForm(request.POST)
        if form.is_valid():
            alias = form.save()

            AuditLog.objects.create(
                user=request.user,
                action="create",
                model_name="LocationAlias",
                object_id=str(alias.id),
                notes="Location alias created from Gazetteer Manager",
                after_data={
                    "alias": alias.alias,
                    "location_id": alias.location_id,
                },
            )

            messages.success(request, f'Alias "{alias.alias}" berhasil ditambahkan.')
            return redirect("intel:gazetteer_alias_manager")
    else:
        form = LocationAliasForm()

    return render(request, "intel/gazetteer_alias_form.html", {
        "page_title": "Tambah Alias",
        "form": form,
        "mode": "create",
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def gazetteer_toggle_false_positive(request, pk):
    loc = get_object_or_404(Location, pk=pk)
    old_value = loc.is_false_positive
    loc.is_false_positive = not loc.is_false_positive
    loc.save()

    AuditLog.objects.create(
        user=request.user,
        action="manual_edit",
        model_name="Location",
        object_id=str(loc.id),
        notes="Toggle false positive from Gazetteer Manager",
        before_data={"is_false_positive": old_value},
        after_data={"is_false_positive": loc.is_false_positive},
    )

    state = "false positive" if loc.is_false_positive else "normal"
    messages.success(request, f'Location "{loc.display_name or loc.name}" diubah menjadi {state}.')
    return redirect(request.META.get("HTTP_REFERER", "intel:gazetteer_manager"))


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def gazetteer_toggle_active(request, pk):
    loc = get_object_or_404(Location, pk=pk)
    old_value = loc.is_active
    loc.is_active = not loc.is_active
    loc.save()

    AuditLog.objects.create(
        user=request.user,
        action="manual_edit",
        model_name="Location",
        object_id=str(loc.id),
        notes="Toggle active state from Gazetteer Manager",
        before_data={"is_active": old_value},
        after_data={"is_active": loc.is_active},
    )

    state = "aktif" if loc.is_active else "nonaktif"
    messages.success(request, f'Location "{loc.display_name or loc.name}" sekarang {state}.')
    return redirect(request.META.get("HTTP_REFERER", "intel:gazetteer_manager"))

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def scoring_rules_manager(request):
    qs = ScoringRule.objects.all()

    q = request.GET.get("q", "").strip()
    rule_type = request.GET.get("rule_type", "").strip()
    is_active = request.GET.get("is_active", "").strip()

    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(keyword__icontains=q)
            | Q(notes__icontains=q)
        )

    if rule_type:
        qs = qs.filter(rule_type=rule_type)

    if is_active == "yes":
        qs = qs.filter(is_active=True)
    elif is_active == "no":
        qs = qs.filter(is_active=False)

    qs = qs.order_by("name")

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    settings_qs = SystemSetting.objects.all().order_by("key")

    return render(request, "intel/scoring_rules_manager.html", {
        "page_title": "Scoring Rules Manager",
        "page_obj": page_obj,
        "q": q,
        "rule_type": rule_type,
        "is_active": is_active,
        "rule_type_choices": ScoringRule.RULE_TYPE_CHOICES,
        "settings_qs": settings_qs,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def scoring_rule_create(request):
    if request.method == "POST":
        form = ScoringRuleForm(request.POST)
        if form.is_valid():
            rule = form.save()

            AuditLog.objects.create(
                user=request.user,
                action="create",
                model_name="ScoringRule",
                object_id=str(rule.id),
                notes="Scoring rule created",
                after_data={
                    "name": rule.name,
                    "rule_type": rule.rule_type,
                    "keyword": rule.keyword,
                    "weight": rule.weight,
                    "is_active": rule.is_active,
                },
            )

            messages.success(request, f'Rule "{rule.name}" berhasil ditambahkan.')
            return redirect("intel:scoring_rules_manager")
    else:
        form = ScoringRuleForm()

    return render(request, "intel/scoring_rule_form.html", {
        "page_title": "Tambah Scoring Rule",
        "form": form,
        "mode": "create",
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def scoring_rule_edit(request, pk):
    rule = get_object_or_404(ScoringRule, pk=pk)

    if request.method == "POST":
        before_data = {
            "name": rule.name,
            "rule_type": rule.rule_type,
            "keyword": rule.keyword,
            "weight": rule.weight,
            "is_active": rule.is_active,
        }

        form = ScoringRuleForm(request.POST, instance=rule)
        if form.is_valid():
            rule = form.save()

            AuditLog.objects.create(
                user=request.user,
                action="update",
                model_name="ScoringRule",
                object_id=str(rule.id),
                notes="Scoring rule updated",
                before_data=before_data,
                after_data={
                    "name": rule.name,
                    "rule_type": rule.rule_type,
                    "keyword": rule.keyword,
                    "weight": rule.weight,
                    "is_active": rule.is_active,
                },
            )

            messages.success(request, f'Rule "{rule.name}" berhasil diperbarui.')
            return redirect("intel:scoring_rules_manager")
    else:
        form = ScoringRuleForm(instance=rule)

    return render(request, "intel/scoring_rule_form.html", {
        "page_title": "Edit Scoring Rule",
        "form": form,
        "mode": "edit",
        "rule_obj": rule,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def scoring_rule_toggle_active(request, pk):
    rule = get_object_or_404(ScoringRule, pk=pk)
    old_value = rule.is_active
    rule.is_active = not rule.is_active
    rule.save()

    AuditLog.objects.create(
        user=request.user,
        action="manual_edit",
        model_name="ScoringRule",
        object_id=str(rule.id),
        notes="Toggle scoring rule active state",
        before_data={"is_active": old_value},
        after_data={"is_active": rule.is_active},
    )

    state = "aktif" if rule.is_active else "nonaktif"
    messages.success(request, f'Rule "{rule.name}" sekarang {state}.')
    return redirect(request.META.get("HTTP_REFERER", "intel:scoring_rules_manager"))


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def system_setting_create(request):
    if request.method == "POST":
        form = SystemSettingForm(request.POST)
        if form.is_valid():
            setting = form.save()

            AuditLog.objects.create(
                user=request.user,
                action="create",
                model_name="SystemSetting",
                object_id=str(setting.id),
                notes="System setting created",
                after_data={
                    "key": setting.key,
                    "value": setting.value,
                    "is_active": setting.is_active,
                },
            )

            messages.success(request, f'Setting "{setting.key}" berhasil ditambahkan.')
            return redirect("intel:scoring_rules_manager")
    else:
        form = SystemSettingForm()

    return render(request, "intel/system_setting_form.html", {
        "page_title": "Tambah System Setting",
        "form": form,
        "mode": "create",
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def system_setting_edit(request, pk):
    setting = get_object_or_404(SystemSetting, pk=pk)

    if request.method == "POST":
        before_data = {
            "key": setting.key,
            "value": setting.value,
            "is_active": setting.is_active,
        }

        form = SystemSettingForm(request.POST, instance=setting)
        if form.is_valid():
            setting = form.save()

            AuditLog.objects.create(
                user=request.user,
                action="update",
                model_name="SystemSetting",
                object_id=str(setting.id),
                notes="System setting updated",
                before_data=before_data,
                after_data={
                    "key": setting.key,
                    "value": setting.value,
                    "is_active": setting.is_active,
                },
            )

            messages.success(request, f'Setting "{setting.key}" berhasil diperbarui.')
            return redirect("intel:scoring_rules_manager")
    else:
        form = SystemSettingForm(instance=setting)

    return render(request, "intel/system_setting_form.html", {
        "page_title": "Edit System Setting",
        "form": form,
        "mode": "edit",
        "setting_obj": setting,
    })

# @login_required
# @role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
# def alert_center(request):
#     qs = Alert.objects.select_related("location").all()

#     status_filter = request.GET.get("status", "").strip()
#     alert_type = request.GET.get("alert_type", "").strip()

#     if status_filter:
#         qs = qs.filter(status=status_filter)

#     if alert_type:
#         qs = qs.filter(alert_type=alert_type)

#     qs = qs.order_by("-created_at")

#     paginator = Paginator(qs, 25)
#     page_number = request.GET.get("page")
#     page_obj = paginator.get_page(page_number)

#     return render(request, "intel/alert_center.html", {
#         "page_title": "Alert Center",
#         "page_obj": page_obj,
#         "status_filter": status_filter,
#         "alert_type": alert_type,
#         "alert_type_choices": Alert.ALERT_TYPE_CHOICES,
#         "status_choices": Alert.STATUS_CHOICES,
#     })


# @login_required
# @role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
# def generate_alerts(request):
#     now = timezone.now()

#     city_signal_threshold = int(get_system_setting_value("alert_city_signal_count", 5))
#     alert_window_hours = int(get_system_setting_value("alert_window_hours", 48))
#     avg_score_threshold = float(get_system_setting_value("alert_avg_score_threshold", 60))

#     since_time = now - timedelta(hours=alert_window_hours)

#     verified_qs = Signal.objects.filter(
#         published_at__gte=since_time,
#         status__in=["validated", "approved"],
#     )

#     # 1. Cluster in city within 48h
#     city_clusters = (
#         SignalLocation.objects.filter(
#             signal__in=verified_qs,
#             is_primary=True,
#             location__isnull=False,
#             location__level__in=["city", "regency"],
#         )
#         .values("location_id", "location__display_name")
#         .annotate(
#             total=Count("id"),
#             avg_score=Avg("signal__threat_score"),
#             first_signal_at=Min("signal__published_at"),
#             last_signal_at=Max("signal__published_at"),
#         )
#         .filter(total__gte=city_signal_threshold)
#         .order_by("-total")
#     )

#     created_count = 0

#     for item in city_clusters:
#         location_id = item["location_id"]
#         loc_name = item["location__display_name"] or "Unknown Location"
#         dedup_key = f"cluster_city_48h::{location_id}::{since_time.date()}::{item['total']}"

#         _, created = Alert.objects.get_or_create(
#             dedup_key=dedup_key,
#             defaults={
#                 "alert_type": "cluster_city_48h",
#                 "title": f"Cluster signal di {loc_name}",
#                 "description": f"Terdeteksi {item['total']} signal dalam {alert_window_hours} jam terakhir di {loc_name}.",
#                 "location_id": location_id,
#                 "signal_count": item["total"],
#                 "avg_score": round(item["avg_score"] or 0, 2),
#                 "status": "open",
#                 "first_signal_at": item["first_signal_at"],
#                 "last_signal_at": item["last_signal_at"],
#                 "rule_key": "cluster_city_48h",
#             },
#         )
#         if created:
#             created_count += 1

#     # 2. High average score in city/regency
#     high_avg_groups = (
#         SignalLocation.objects.filter(
#             signal__in=verified_qs,
#             is_primary=True,
#             location__isnull=False,
#             location__level__in=["city", "regency"],
#         )
#         .values("location_id", "location__display_name")
#         .annotate(
#             total=Count("id"),
#             avg_score=Avg("signal__threat_score"),
#             first_signal_at=Min("signal__published_at"),
#             last_signal_at=Max("signal__published_at"),
#         )
#         .filter(avg_score__gt=avg_score_threshold, total__gte=2)
#         .order_by("-avg_score")
#     )

#     for item in high_avg_groups:
#         location_id = item["location_id"]
#         loc_name = item["location__display_name"] or "Unknown Location"
#         dedup_key = f"high_avg_score::{location_id}::{since_time.date()}::{round(item['avg_score'] or 0, 2)}"

#         _, created = Alert.objects.get_or_create(
#             dedup_key=dedup_key,
#             defaults={
#                 "alert_type": "high_avg_score",
#                 "title": f"Rata-rata skor tinggi di {loc_name}",
#                 "description": f"Rata-rata skor {round(item['avg_score'] or 0, 2)} pada {item['total']} signal di {loc_name}.",
#                 "location_id": location_id,
#                 "signal_count": item["total"],
#                 "avg_score": round(item["avg_score"] or 0, 2),
#                 "status": "open",
#                 "first_signal_at": item["first_signal_at"],
#                 "last_signal_at": item["last_signal_at"],
#                 "rule_key": "high_avg_score",
#             },
#         )
#         if created:
#             created_count += 1

#     # 3. New location appeared recently
#     recent_locations = (
#         SignalLocation.objects.filter(
#             signal__in=verified_qs,
#             is_primary=True,
#             location__isnull=False,
#         )
#         .values("location_id", "location__display_name")
#         .annotate(
#             first_signal_at=Min("signal__published_at"),
#             total=Count("id"),
#             avg_score=Avg("signal__threat_score"),
#         )
#     )

#     for item in recent_locations:
#         location_id = item["location_id"]
#         loc_name = item["location__display_name"] or "Unknown Location"

#         older_exists = SignalLocation.objects.filter(
#             location_id=location_id,
#             is_primary=True,
#             signal__published_at__lt=since_time,
#             signal__status__in=["validated", "approved"],
#         ).exists()

#         if not older_exists:
#             dedup_key = f"new_location::{location_id}::{since_time.date()}"

#             _, created = Alert.objects.get_or_create(
#                 dedup_key=dedup_key,
#                 defaults={
#                     "alert_type": "new_location",
#                     "title": f"Lokasi baru muncul: {loc_name}",
#                     "description": f"Lokasi {loc_name} muncul sebagai signal terverifikasi dalam {alert_window_hours} jam terakhir.",
#                     "location_id": location_id,
#                     "signal_count": item["total"],
#                     "avg_score": round(item["avg_score"] or 0, 2),
#                     "status": "open",
#                     "first_signal_at": item["first_signal_at"],
#                     "last_signal_at": item["first_signal_at"],
#                     "rule_key": "new_location",
#                 },
#             )
#             if created:
#                 created_count += 1

#     messages.success(request, f"Generate alert selesai. Alert baru dibuat: {created_count}.")
#     return redirect("intel:alert_center")


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def alert_update_status(request, pk):
    alert = get_object_or_404(Alert, pk=pk)

    if request.method == "POST":
        form = AlertStatusForm(request.POST, instance=alert)
        if form.is_valid():
            before_data = {
                "status": alert.status,
                "description": alert.description,
            }
            alert = form.save()

            AuditLog.objects.create(
                user=request.user,
                action="manual_edit",
                model_name="Alert",
                object_id=str(alert.id),
                notes="Alert status updated",
                before_data=before_data,
                after_data={
                    "status": alert.status,
                    "description": alert.description,
                },
            )

            messages.success(request, f'Alert "{alert.title}" berhasil diperbarui.')
            return redirect("intel:alert_center")
    else:
        form = AlertStatusForm(instance=alert)

    return render(request, "intel/alert_form.html", {
        "page_title": "Update Alert",
        "form": form,
        "alert_obj": alert,
    })

def _alert_type_choices_extended():
    """Tambahan label alert tanpa perlu mengubah model choices/migration."""
    base = list(getattr(Alert, "ALERT_TYPE_CHOICES", []))
    extra = [
        ("disease_spike_province", "Disease Spike Province"),
        ("disease_cluster_city", "Disease Cluster City"),
        ("new_disease_location", "New Disease Location"),
    ]
    existing = {key for key, _ in base}
    for item in extra:
        if item[0] not in existing:
            base.append(item)
    return base


def _alert_level_from_stats(total, avg_score, high_risk_total=0, increase_ratio=0):
    total = total or 0
    avg_score = avg_score or 0
    high_risk_total = high_risk_total or 0
    increase_ratio = increase_ratio or 0

    if high_risk_total >= 3 or avg_score >= 70 or increase_ratio >= 3:
        return "Merah"
    if high_risk_total >= 1 or avg_score >= 55 or increase_ratio >= 2:
        return "Oranye"
    if total >= 3 or avg_score >= 40:
        return "Kuning"
    return "Hijau"


def _location_to_province(loc):
    if not loc:
        return None
    if getattr(loc, "level", "") == "province":
        return loc
    parent = getattr(loc, "parent", None)
    if parent and getattr(parent, "level", "") == "province":
        return parent
    return None


def _location_to_city(loc):
    if not loc:
        return None
    if getattr(loc, "level", "") in ["city", "regency"]:
        return loc
    return None


def _build_disease_location_stats(signal_qs, target_level="province"):
    """
    Agregasi SignalLocation ke level provinsi/kabkota + penyakit.
    Return dict dengan key (location_id, disease_tag).
    """
    links = (
        SignalLocation.objects.filter(
            signal__in=signal_qs,
            is_primary=True,
            location__isnull=False,
        )
        .select_related("signal", "signal__source", "location", "location__parent")
        .order_by("-signal__threat_score", "-signal__published_at")
    )

    stats = {}
    seen = set()

    for link in links:
        signal = link.signal
        loc = link.location
        disease = (signal.disease_tag or "Tidak diketahui").strip() or "Tidak diketahui"

        if target_level == "province":
            target_loc = _location_to_province(loc)
        else:
            target_loc = _location_to_city(loc)

        if not target_loc:
            continue

        key = (target_loc.id, disease.lower())
        seen_key = (signal.id, target_loc.id, disease.lower())
        if seen_key in seen:
            continue
        seen.add(seen_key)

        if key not in stats:
            stats[key] = {
                "location": target_loc,
                "location_id": target_loc.id,
                "location_name": target_loc.display_name or target_loc.name or "Unknown Location",
                "disease_tag": disease,
                "total": 0,
                "high_risk_total": 0,
                "score_sum": 0.0,
                "first_signal_at": None,
                "last_signal_at": None,
                "sample_signals": [],
            }

        item = stats[key]
        score = signal.threat_score or 0
        item["total"] += 1
        item["score_sum"] += score
        if score >= 70:
            item["high_risk_total"] += 1

        if signal.published_at:
            if item["first_signal_at"] is None or signal.published_at < item["first_signal_at"]:
                item["first_signal_at"] = signal.published_at
            if item["last_signal_at"] is None or signal.published_at > item["last_signal_at"]:
                item["last_signal_at"] = signal.published_at

        if len(item["sample_signals"]) < 3:
            item["sample_signals"].append({
                "id": signal.id,
                "title": signal.title or "-",
                "score": score,
                "date": signal.published_at.strftime("%Y-%m-%d") if signal.published_at else "-",
                "source": signal.source.name if signal.source else "-",
            })

    for item in stats.values():
        item["avg_score"] = round(item["score_sum"] / item["total"], 2) if item["total"] else 0

    return stats


def _create_or_update_alert(*, alert_type, title, description, location, signal_count, avg_score, first_signal_at, last_signal_at, rule_key, dedup_key):
    """
    Create alert baru atau update alert open yang sudah ada dengan dedup_key sama.
    """
    alert, created = Alert.objects.get_or_create(
        dedup_key=dedup_key,
        defaults={
            "alert_type": alert_type,
            "title": title,
            "description": description,
            "location": location,
            "signal_count": signal_count,
            "avg_score": avg_score,
            "status": "open",
            "first_signal_at": first_signal_at,
            "last_signal_at": last_signal_at,
            "rule_key": rule_key,
        },
    )

    if not created and alert.status == "open":
        alert.title = title
        alert.description = description
        alert.location = location
        alert.signal_count = signal_count
        alert.avg_score = avg_score
        alert.first_signal_at = first_signal_at
        alert.last_signal_at = last_signal_at
        alert.rule_key = rule_key
        alert.save(update_fields=[
            "title", "description", "location", "signal_count", "avg_score",
            "first_signal_at", "last_signal_at", "rule_key", "updated_at"
        ])

    return created


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def alert_center(request):
    qs = Alert.objects.select_related("location").all()

    status_filter = (request.GET.get("status") or "").strip()
    alert_type = (request.GET.get("alert_type") or "").strip()

    if status_filter:
        qs = qs.filter(status=status_filter)

    if alert_type:
        qs = qs.filter(alert_type=alert_type)

    qs = qs.order_by("-created_at")

    alert_rows = []
    for alert in qs:
        loc = getattr(alert, "location", None)
        location_name = "-"
        if loc:
            location_name = getattr(loc, "display_name", "") or getattr(loc, "name", "") or "-"

        avg_score = alert.avg_score or 0
        signal_count = alert.signal_count or 0

        if avg_score >= 70 or signal_count >= 10:
            visual_level = "Merah"
        elif avg_score >= 60 or signal_count >= 5:
            visual_level = "Oranye"
        elif avg_score >= 40 or signal_count >= 2:
            visual_level = "Kuning"
        else:
            visual_level = "Hijau"

        alert_rows.append({
            "id": alert.id,
            "alert_type": alert.alert_type or "-",
            "title": alert.title or "-",
            "description": alert.description or "-",
            "location_name": location_name,
            "signal_count": signal_count,
            "avg_score": round(avg_score, 2),
            "status": alert.status or "open",
            "first_signal_at": alert.first_signal_at,
            "last_signal_at": alert.last_signal_at,
            "rule_key": alert.rule_key or "-",
            "visual_level": visual_level,
        })

    paginator = Paginator(alert_rows, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    alert_type_choices = list(getattr(Alert, "ALERT_TYPE_CHOICES", []))
    status_choices = list(getattr(Alert, "STATUS_CHOICES", []))

    return render(request, "intel/alert_center.html", {
        "page_title": "Alert Center",
        "page_obj": page_obj,
        "status_filter": status_filter,
        "alert_type": alert_type,
        "alert_type_choices": alert_type_choices,
        "status_choices": status_choices,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def generate_alerts(request):
    """
    Upgrade generate alert:
    1. mempertahankan alert lama: city cluster, high avg score, new location
    2. menambahkan disease spike provinsi berbasis pembanding baseline
    3. menambahkan disease cluster kab/kota
    4. menambahkan new disease-location
    """
    now = timezone.now()

    city_signal_threshold = int(get_system_setting_value("alert_city_signal_count", 5))
    alert_window_hours = int(get_system_setting_value("alert_window_hours", 48))
    avg_score_threshold = float(get_system_setting_value("alert_avg_score_threshold", 60))

    recent_days = int(get_system_setting_value("alert_recent_days", 7))
    baseline_days = int(get_system_setting_value("alert_baseline_days", 21))
    disease_signal_threshold = int(get_system_setting_value("alert_disease_signal_count", 5))
    spike_ratio_threshold = float(get_system_setting_value("alert_spike_ratio", 2.0))

    since_time = now - timedelta(hours=alert_window_hours)
    recent_start = now - timedelta(days=recent_days)
    baseline_start = recent_start - timedelta(days=baseline_days)

    verified_recent_qs = Signal.objects.filter(
        published_at__gte=since_time,
        status__in=["validated", "approved"],
    ).exclude(status="noise")

    verified_window_qs = Signal.objects.filter(
        published_at__gte=recent_start,
        published_at__lte=now,
        status__in=["validated", "approved"],
    ).exclude(status="noise")

    verified_baseline_qs = Signal.objects.filter(
        published_at__gte=baseline_start,
        published_at__lt=recent_start,
        status__in=["validated", "approved"],
    ).exclude(status="noise")

    created_count = 0
    updated_or_existing_count = 0

    # ------------------------------------------------------------
    # 1. Existing: Cluster in city within alert window
    # ------------------------------------------------------------
    city_clusters = (
        SignalLocation.objects.filter(
            signal__in=verified_recent_qs,
            is_primary=True,
            location__isnull=False,
            location__level__in=["city", "regency"],
        )
        .values("location_id", "location__display_name")
        .annotate(
            total=Count("id"),
            avg_score=Avg("signal__threat_score"),
            first_signal_at=Min("signal__published_at"),
            last_signal_at=Max("signal__published_at"),
        )
        .filter(total__gte=city_signal_threshold)
        .order_by("-total")
    )

    for item in city_clusters:
        location_id = item["location_id"]
        loc_name = item["location__display_name"] or "Unknown Location"
        loc = Location.objects.filter(id=location_id).first()
        dedup_key = f"cluster_city_48h::{location_id}::{since_time.date()}"
        created = _create_or_update_alert(
            alert_type="cluster_city_48h",
            title=f"Cluster signal di {loc_name}",
            description=f"Terdeteksi {item['total']} signal dalam {alert_window_hours} jam terakhir di {loc_name}.",
            location=loc,
            signal_count=item["total"],
            avg_score=round(item["avg_score"] or 0, 2),
            first_signal_at=item["first_signal_at"],
            last_signal_at=item["last_signal_at"],
            rule_key="cluster_city_48h",
            dedup_key=dedup_key,
        )
        created_count += 1 if created else 0
        updated_or_existing_count += 0 if created else 1

    # ------------------------------------------------------------
    # 2. Existing: High average score city/regency
    # ------------------------------------------------------------
    high_avg_groups = (
        SignalLocation.objects.filter(
            signal__in=verified_recent_qs,
            is_primary=True,
            location__isnull=False,
            location__level__in=["city", "regency"],
        )
        .values("location_id", "location__display_name")
        .annotate(
            total=Count("id"),
            avg_score=Avg("signal__threat_score"),
            first_signal_at=Min("signal__published_at"),
            last_signal_at=Max("signal__published_at"),
        )
        .filter(avg_score__gt=avg_score_threshold, total__gte=2)
        .order_by("-avg_score")
    )

    for item in high_avg_groups:
        location_id = item["location_id"]
        loc_name = item["location__display_name"] or "Unknown Location"
        loc = Location.objects.filter(id=location_id).first()
        dedup_key = f"high_avg_score::{location_id}::{since_time.date()}"
        created = _create_or_update_alert(
            alert_type="high_avg_score",
            title=f"Rata-rata skor tinggi di {loc_name}",
            description=f"Rata-rata skor {round(item['avg_score'] or 0, 2)} pada {item['total']} signal di {loc_name}.",
            location=loc,
            signal_count=item["total"],
            avg_score=round(item["avg_score"] or 0, 2),
            first_signal_at=item["first_signal_at"],
            last_signal_at=item["last_signal_at"],
            rule_key="high_avg_score",
            dedup_key=dedup_key,
        )
        created_count += 1 if created else 0
        updated_or_existing_count += 0 if created else 1

    # ------------------------------------------------------------
    # 3. Existing: New location appeared recently
    # ------------------------------------------------------------
    recent_locations = (
        SignalLocation.objects.filter(
            signal__in=verified_recent_qs,
            is_primary=True,
            location__isnull=False,
        )
        .values("location_id", "location__display_name")
        .annotate(
            first_signal_at=Min("signal__published_at"),
            last_signal_at=Max("signal__published_at"),
            total=Count("id"),
            avg_score=Avg("signal__threat_score"),
        )
    )

    for item in recent_locations:
        location_id = item["location_id"]
        loc_name = item["location__display_name"] or "Unknown Location"
        older_exists = SignalLocation.objects.filter(
            location_id=location_id,
            is_primary=True,
            signal__published_at__lt=since_time,
            signal__status__in=["validated", "approved"],
        ).exists()

        if not older_exists:
            loc = Location.objects.filter(id=location_id).first()
            dedup_key = f"new_location::{location_id}::{since_time.date()}"
            created = _create_or_update_alert(
                alert_type="new_location",
                title=f"Lokasi baru muncul: {loc_name}",
                description=f"Lokasi {loc_name} muncul sebagai signal terverifikasi dalam {alert_window_hours} jam terakhir.",
                location=loc,
                signal_count=item["total"],
                avg_score=round(item["avg_score"] or 0, 2),
                first_signal_at=item["first_signal_at"],
                last_signal_at=item["last_signal_at"],
                rule_key="new_location",
                dedup_key=dedup_key,
            )
            created_count += 1 if created else 0
            updated_or_existing_count += 0 if created else 1

    # ------------------------------------------------------------
    # 4. New: Disease spike at province level
    # ------------------------------------------------------------
    recent_province_stats = _build_disease_location_stats(verified_window_qs, target_level="province")
    baseline_province_stats = _build_disease_location_stats(verified_baseline_qs, target_level="province")

    baseline_factor = max(1, baseline_days / max(1, recent_days))

    for key, recent in recent_province_stats.items():
        baseline = baseline_province_stats.get(key, {"total": 0})
        baseline_expected = (baseline.get("total", 0) / baseline_factor) if baseline_factor else 0
        baseline_expected_for_ratio = max(1, baseline_expected)
        increase_ratio = round((recent["total"] / baseline_expected_for_ratio), 2)

        if recent["total"] < disease_signal_threshold:
            continue
        if increase_ratio < spike_ratio_threshold and recent["high_risk_total"] <= 0 and recent["avg_score"] < avg_score_threshold:
            continue

        loc = recent["location"]
        loc_name = recent["location_name"]
        disease = recent["disease_tag"]
        level = _alert_level_from_stats(recent["total"], recent["avg_score"], recent["high_risk_total"], increase_ratio)
        dedup_key = f"disease_spike_province::{loc.id}::{disease.lower()}::{recent_start.date()}::{now.date()}"
        description = (
            f"{level}: {disease} meningkat di {loc_name}. "
            f"Periode {recent_days} hari terakhir mencatat {recent['total']} signal, "
            f"baseline ekuivalen sekitar {round(baseline_expected, 2)} signal, "
            f"rasio kenaikan {increase_ratio}x, high-risk {recent['high_risk_total']}, "
            f"dan rata-rata skor {recent['avg_score']}."
        )
        created = _create_or_update_alert(
            alert_type="disease_spike_province",
            title=f"Lonjakan {disease} di {loc_name}",
            description=description,
            location=loc,
            signal_count=recent["total"],
            avg_score=recent["avg_score"],
            first_signal_at=recent["first_signal_at"],
            last_signal_at=recent["last_signal_at"],
            rule_key="disease_spike_province",
            dedup_key=dedup_key,
        )
        created_count += 1 if created else 0
        updated_or_existing_count += 0 if created else 1

    # ------------------------------------------------------------
    # 5. New: Disease cluster at city/regency level
    # ------------------------------------------------------------
    recent_city_stats = _build_disease_location_stats(verified_window_qs, target_level="city")

    for key, recent in recent_city_stats.items():
        if recent["total"] < max(2, city_signal_threshold):
            continue
        if recent["avg_score"] < 35 and recent["high_risk_total"] <= 0:
            continue

        loc = recent["location"]
        loc_name = recent["location_name"]
        disease = recent["disease_tag"]
        level = _alert_level_from_stats(recent["total"], recent["avg_score"], recent["high_risk_total"], 0)
        dedup_key = f"disease_cluster_city::{loc.id}::{disease.lower()}::{recent_start.date()}::{now.date()}"
        description = (
            f"{level}: Terdapat cluster {disease} di {loc_name} dengan {recent['total']} signal "
            f"dalam {recent_days} hari terakhir, high-risk {recent['high_risk_total']}, "
            f"dan rata-rata skor {recent['avg_score']}."
        )
        created = _create_or_update_alert(
            alert_type="disease_cluster_city",
            title=f"Cluster {disease} di {loc_name}",
            description=description,
            location=loc,
            signal_count=recent["total"],
            avg_score=recent["avg_score"],
            first_signal_at=recent["first_signal_at"],
            last_signal_at=recent["last_signal_at"],
            rule_key="disease_cluster_city",
            dedup_key=dedup_key,
        )
        created_count += 1 if created else 0
        updated_or_existing_count += 0 if created else 1

    # ------------------------------------------------------------
    # 6. New: New disease-location combination
    # ------------------------------------------------------------
    for key, recent in recent_city_stats.items():
        loc = recent["location"]
        disease = recent["disease_tag"]
        older_same_disease_exists = SignalLocation.objects.filter(
            location_id=loc.id,
            is_primary=True,
            signal__disease_tag__iexact=disease,
            signal__published_at__lt=recent_start,
            signal__status__in=["validated", "approved"],
        ).exists()

        if older_same_disease_exists:
            continue
        if recent["total"] < 2 and recent["avg_score"] < avg_score_threshold:
            continue

        dedup_key = f"new_disease_location::{loc.id}::{disease.lower()}::{recent_start.date()}"
        description = (
            f"Kombinasi penyakit-lokasi baru: {disease} muncul di {recent['location_name']} "
            f"pada periode {recent_days} hari terakhir dengan {recent['total']} signal dan rata-rata skor {recent['avg_score']}."
        )
        created = _create_or_update_alert(
            alert_type="new_disease_location",
            title=f"{disease} muncul di lokasi baru: {recent['location_name']}",
            description=description,
            location=loc,
            signal_count=recent["total"],
            avg_score=recent["avg_score"],
            first_signal_at=recent["first_signal_at"],
            last_signal_at=recent["last_signal_at"],
            rule_key="new_disease_location",
            dedup_key=dedup_key,
        )
        created_count += 1 if created else 0
        updated_or_existing_count += 0 if created else 1

    messages.success(
        request,
        f"Generate alert selesai. Alert baru dibuat: {created_count}. Alert existing/update: {updated_or_existing_count}."
    )
    return redirect("intel:alert_center")


@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def map_intelligence(request):
    disease = request.GET.get("disease", "").strip()
    min_score = request.GET.get("min_score", "20").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    status_filter = request.GET.get("status", "approved").strip()

    try:
        min_score_val = int(min_score)
    except ValueError:
        min_score_val = 35

    disease_choices = (
        Signal.objects.exclude(disease_tag="")
        .values_list("disease_tag", flat=True)
        .distinct()
        .order_by("disease_tag")
    )

    context = {
        "page_title": "Map Intelligence",
        "disease": disease,
        "min_score": min_score_val,
        "date_from": date_from,
        "date_to": date_to,
        "status_filter": status_filter,
        "disease_choices": disease_choices,
    }
    return render(request, "intel/map_intelligence.html", context)


@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def map_points_api(request):
    disease = request.GET.get("disease", "").strip()
    min_score = request.GET.get("min_score", "35").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    status_filter = request.GET.get("status", "approved").strip()

    try:
        min_score_val = int(min_score)
    except ValueError:
        min_score_val = 35

    valid_statuses = ["validated", "approved"]
    if status_filter == "approved":
        valid_statuses = ["approved"]
    elif status_filter == "validated":
        valid_statuses = ["validated"]
    elif status_filter == "all":
        valid_statuses = ["validated", "approved"]

    qs = (
        SignalLocation.objects.filter(
            is_primary=True,
            location__isnull=False,
            location__lat__isnull=False,
            location__lon__isnull=False,
            signal__status__in=valid_statuses,
            signal__threat_score__gte=min_score_val,
        )
        .select_related("signal", "signal__source", "location")
        .order_by("-signal__published_at", "-signal__created_at")
    )

    if disease:
        qs = qs.filter(signal__disease_tag__iexact=disease)

    if date_from:
        qs = qs.filter(signal__published_at__date__gte=date_from)

    if date_to:
        qs = qs.filter(signal__published_at__date__lte=date_to)

    data = []
    for item in qs[:2000]:
        signal = item.signal
        location = item.location

        data.append({
            "id": signal.id,
            "title": signal.title,
            "disease_tag": signal.disease_tag or "",
            "threat_score": signal.threat_score,
            "status": signal.status,
            "geocode_status": signal.geocode_status,
            "approved_for_mapping": signal.approved_for_mapping,
            "raw_location_text": signal.raw_location_text or "",
            "published_at": signal.published_at.isoformat() if signal.published_at else "",
            "source_name": signal.source.name if signal.source else "",
            "source_url": signal.source_url,
            "lat": location.lat,
            "lon": location.lon,
            "location_name": location.display_name or location.name,
            "location_level": location.level,
            "province_code": location.province_code or "",
        })

    return JsonResponse({
        "count": len(data),
        "results": data,
    })

@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def map_thematic(request):
    metric = request.GET.get("metric", "signal_count").strip()
    disease = request.GET.get("disease", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    status_filter = request.GET.get("status", "approved").strip()
    level = request.GET.get("level", "province").strip()

    disease_choices = (
        Signal.objects.exclude(disease_tag="")
        .values_list("disease_tag", flat=True)
        .distinct()
        .order_by("disease_tag")
    )

    return render(request, "intel/map_thematic.html", {
        "page_title": "Thematic Map",
        "metric": metric,
        "disease": disease,
        "date_from": date_from,
        "date_to": date_to,
        "status_filter": status_filter,
        "level": level,
        "disease_choices": disease_choices,
    })

@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def map_thematic_data_api(request):
    metric = request.GET.get("metric", "signal_count").strip()
    disease = request.GET.get("disease", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    status_filter = request.GET.get("status", "approved").strip()
    level = request.GET.get("level", "province").strip()

    valid_statuses = ["approved"]
    if status_filter == "validated":
        valid_statuses = ["validated"]
    elif status_filter == "all":
        valid_statuses = ["validated", "approved"]

    qs = SignalLocation.objects.filter(
        is_primary=True,
        location__isnull=False,
        signal__status__in=valid_statuses,
    ).select_related("signal", "location")

    if disease:
        qs = qs.filter(signal__disease_tag__iexact=disease)

    if date_from:
        qs = qs.filter(signal__published_at__date__gte=date_from)

    if date_to:
        qs = qs.filter(signal__published_at__date__lte=date_to)

    if level == "province":
        rows = (
            qs.exclude(location__province_code="")
            .values("location__province_code")
            .annotate(
                signal_count=Count("id"),
                avg_score=Avg("signal__threat_score"),
                high_risk_count=Count("id", filter=Q(signal__threat_score__gt=50)),
            )
            .order_by("-signal_count")
        )

        results = []
        for row in rows:
            results.append({
                "region_key": row["location__province_code"],
                "signal_count": row["signal_count"] or 0,
                "avg_score": round(row["avg_score"] or 0, 2),
                "high_risk_count": row["high_risk_count"] or 0,
            })

        return JsonResponse({"results": results})

    elif level == "kabkota":
        rows = (
            qs.exclude(location__city_regency_code="")
            .values("location__city_regency_code")
            .annotate(
                signal_count=Count("id"),
                avg_score=Avg("signal__threat_score"),
                high_risk_count=Count("id", filter=Q(signal__threat_score__gt=50)),
            )
            .order_by("-signal_count")
        )

        results = []
        for row in rows:
            results.append({
                "region_key": row["location__city_regency_code"],
                "signal_count": row["signal_count"] or 0,
                "avg_score": round(row["avg_score"] or 0, 2),
                "high_risk_count": row["high_risk_count"] or 0,
            })

        return JsonResponse({"results": results})

    return JsonResponse({"results": []})
# =========================================================
# PATCHED BACK: GAZETTEER LOCATION MANAGER
# =========================================================
@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def gazetteer_location_manager(request):
    q = request.GET.get("q", "").strip()
    level = request.GET.get("level", "").strip()
    is_active = request.GET.get("is_active", "").strip()
    is_false_positive = request.GET.get("is_false_positive", "").strip()

    qs = Location.objects.select_related("parent").all()

    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(display_name__icontains=q)
            | Q(normalized_name__icontains=q)
            | Q(province_code__icontains=q)
            | Q(city_regency_code__icontains=q)
            | Q(parent__display_name__icontains=q)
            | Q(parent__name__icontains=q)
        )

    if level:
        qs = qs.filter(level=level)

    if is_active == "yes":
        qs = qs.filter(is_active=True)
    elif is_active == "no":
        qs = qs.filter(is_active=False)

    if is_false_positive == "yes":
        qs = qs.filter(is_false_positive=True)
    elif is_false_positive == "no":
        qs = qs.filter(is_false_positive=False)

    qs = qs.order_by("level", "display_name", "name")

    paginator = Paginator(qs, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "intel/gazetteer_location_manager.html", {
        "page_title": "Gazetteer Manager",
        "page_obj": page_obj,
        "q": q,
        "level": level,
        "is_active": is_active,
        "is_false_positive": is_false_positive,
        "level_choices": Location.LEVEL_CHOICES,
    })


# =========================================================
# PATCHED BACK: PUBLISHER ALIAS MANAGER
# =========================================================
def normalize_publisher_alias(value):
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9\s\.\-]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def publisher_alias_manager(request):
    q = request.GET.get("q", "").strip()
    is_active = request.GET.get("is_active", "").strip()

    qs = PublisherDomainAlias.objects.all()

    if q:
        qs = qs.filter(
            Q(alias__icontains=q) |
            Q(normalized_alias__icontains=q) |
            Q(domain__icontains=q) |
            Q(notes__icontains=q)
        )

    if is_active == "yes":
        qs = qs.filter(is_active=True)
    elif is_active == "no":
        qs = qs.filter(is_active=False)

    qs = qs.order_by("alias")

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "intel/publisher_alias_manager.html", {
        "page_title": "Publisher Alias Manager",
        "page_obj": page_obj,
        "q": q,
        "is_active": is_active,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def publisher_alias_create(request):
    if request.method == "POST":
        alias = (request.POST.get("alias") or "").strip()
        domain = (request.POST.get("domain") or "").strip().lower()
        notes = (request.POST.get("notes") or "").strip()
        is_active = request.POST.get("is_active") == "on"

        if not alias or not domain:
            messages.error(request, "Alias dan domain wajib diisi.")
            return redirect("intel:publisher_alias_create")

        normalized_alias = normalize_publisher_alias(alias)

        obj, created = PublisherDomainAlias.objects.update_or_create(
            normalized_alias=normalized_alias,
            defaults={
                "alias": alias,
                "domain": domain,
                "notes": notes,
                "is_active": is_active,
            },
        )

        AuditLog.objects.create(
            user=request.user,
            action="create" if created else "update",
            model_name="PublisherDomainAlias",
            object_id=str(obj.id),
            notes="Publisher alias created/updated from manager",
            after_data={
                "alias": obj.alias,
                "normalized_alias": obj.normalized_alias,
                "domain": obj.domain,
                "is_active": obj.is_active,
            },
        )

        messages.success(request, f'Publisher alias "{alias}" berhasil disimpan.')
        return redirect("intel:publisher_alias_manager")

    return render(request, "intel/publisher_alias_form.html", {
        "page_title": "Tambah Publisher Alias",
        "mode": "create",
        "alias_obj": None,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def publisher_alias_edit(request, pk):
    obj = get_object_or_404(PublisherDomainAlias, pk=pk)

    if request.method == "POST":
        before_data = {
            "alias": obj.alias,
            "normalized_alias": obj.normalized_alias,
            "domain": obj.domain,
            "is_active": obj.is_active,
            "notes": obj.notes,
        }

        alias = (request.POST.get("alias") or "").strip()
        domain = (request.POST.get("domain") or "").strip().lower()
        notes = (request.POST.get("notes") or "").strip()
        is_active = request.POST.get("is_active") == "on"

        if not alias or not domain:
            messages.error(request, "Alias dan domain wajib diisi.")
            return redirect("intel:publisher_alias_edit", pk=obj.id)

        obj.alias = alias
        obj.normalized_alias = normalize_publisher_alias(alias)
        obj.domain = domain
        obj.notes = notes
        obj.is_active = is_active
        obj.save()

        AuditLog.objects.create(
            user=request.user,
            action="update",
            model_name="PublisherDomainAlias",
            object_id=str(obj.id),
            notes="Publisher alias updated from manager",
            before_data=before_data,
            after_data={
                "alias": obj.alias,
                "normalized_alias": obj.normalized_alias,
                "domain": obj.domain,
                "is_active": obj.is_active,
                "notes": obj.notes,
            },
        )

        messages.success(request, f'Publisher alias "{obj.alias}" berhasil diperbarui.')
        return redirect("intel:publisher_alias_manager")

    return render(request, "intel/publisher_alias_form.html", {
        "page_title": "Edit Publisher Alias",
        "mode": "edit",
        "alias_obj": obj,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def publisher_alias_toggle_active(request, pk):
    obj = get_object_or_404(PublisherDomainAlias, pk=pk)

    before_data = {
        "is_active": obj.is_active,
    }

    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active", "updated_at"])

    AuditLog.objects.create(
        user=request.user,
        action="manual_edit",
        model_name="PublisherDomainAlias",
        object_id=str(obj.id),
        notes="Toggle publisher alias active state",
        before_data=before_data,
        after_data={
            "is_active": obj.is_active,
        },
    )

    state = "aktif" if obj.is_active else "nonaktif"
    messages.success(request, f'Publisher alias "{obj.alias}" sekarang {state}.')
    return redirect(request.META.get("HTTP_REFERER", "intel:publisher_alias_manager"))
@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def disease_choropleth_map(request):
    """
    Peta choropleth statis berbasis GeoJSON lokal.
    Tujuan: mengganti peta tile/marker yang bergantung internet menjadi peta tematik
    seperti gambar publikasi ilmiah: wilayah diberi warna berdasarkan metrik.

    Syarat file batas wilayah:
    backend/intel/static/intel/geo/indonesia_provinces.geojson

    Properti GeoJSON yang bisa dibaca:
    - province_code / kode / KODE / Kode / code
    - province_name / name / NAME_1 / Propinsi / PROVINSI
    """
    metric = (request.GET.get("metric") or "signal_count").strip()
    disease = (request.GET.get("disease") or "").strip()
    status_filter = (request.GET.get("status_filter") or "operational").strip()
    days = int(request.GET.get("days") or 30)

    if days not in [7, 14, 30, 60, 90]:
        days = 30

    date_to = (request.GET.get("date_to") or "").strip()
    if date_to:
        date_to_obj = timezone.datetime.fromisoformat(date_to).date()
    else:
        date_to_obj = timezone.now().date()
    date_from_obj = date_to_obj - timedelta(days=days - 1)

    if status_filter == "approved":
        statuses = ["approved"]
        status_label = "Approved Mapping"
    elif status_filter == "validated":
        statuses = ["validated", "approved"]
        status_label = "Validated + Approved"
    elif status_filter == "raw":
        statuses = ["raw"]
        status_label = "Raw Crawling"
    else:
        statuses = ["raw", "validated", "approved"]
        status_label = "Data Operasional Crawling"

    base_qs = (
        SignalLocation.objects.filter(
            is_primary=True,
            location__isnull=False,
            signal__status__in=statuses,
            signal__published_at__date__gte=date_from_obj,
            signal__published_at__date__lte=date_to_obj,
        )
        .exclude(signal__status="noise")
        .select_related("signal", "location", "location__parent", "signal__source")
    )

    if disease:
        base_qs = base_qs.filter(signal__disease_tag__iexact=disease)

    province_map = {}
    seen_signal_per_province = set()

    def _province_from_location(loc):
        if not loc:
            return None
        if loc.level == "province":
            return loc
        if loc.parent and loc.parent.level == "province":
            return loc.parent
        return None

    for link in base_qs:
        signal = link.signal
        loc = link.location
        province = _province_from_location(loc)
        if not province:
            continue

        province_code = province.province_code or normalize_region_code(province.display_name or province.name)
        province_name = province.display_name or province.name or province_code
        key = province_code

        province_map.setdefault(key, {
            "region_key": key,
            "province_code": province_code,
            "province_name": province_name,
            "signal_ids": set(),
            "signal_count": 0,
            "high_risk_count": 0,
            "score_sum": 0,
            "max_score": 0,
            "diseases": {},
            "top_signal_title": "",
            "top_signal_url": "",
            "top_signal_score": 0,
        })

        unique_pair = (key, signal.id)
        if unique_pair in seen_signal_per_province:
            continue
        seen_signal_per_province.add(unique_pair)

        row = province_map[key]
        score = signal.threat_score or 0
        disease_name = signal.disease_tag or "Tidak terklasifikasi"

        row["signal_ids"].add(signal.id)
        row["signal_count"] += 1
        row["score_sum"] += score
        row["max_score"] = max(row["max_score"], score)
        if score >= 70 or signal.is_high_risk:
            row["high_risk_count"] += 1
        row["diseases"][disease_name] = row["diseases"].get(disease_name, 0) + 1

        if score >= row["top_signal_score"]:
            row["top_signal_title"] = signal.title or "-"
            row["top_signal_url"] = signal.source_url or ""
            row["top_signal_score"] = score

    rows = []
    for item in province_map.values():
        total = item["signal_count"]
        avg_score = round(item["score_sum"] / total, 2) if total else 0
        dominant_disease = "-"
        if item["diseases"]:
            dominant_disease = sorted(item["diseases"].items(), key=lambda x: (-x[1], x[0]))[0][0]

        risk_index = round((avg_score * 0.55) + (item["high_risk_count"] * 8) + min(total * 2, 30), 2)
        if risk_index >= 75:
            risk_level = "Tinggi"
        elif risk_index >= 50:
            risk_level = "Sedang-Tinggi"
        elif risk_index >= 30:
            risk_level = "Sedang"
        else:
            risk_level = "Rendah"

        rows.append({
            "region_key": item["region_key"],
            "province_code": item["province_code"],
            "province_name": item["province_name"],
            "signal_count": total,
            "high_risk_count": item["high_risk_count"],
            "avg_score": avg_score,
            "max_score": item["max_score"],
            "risk_index": risk_index,
            "risk_level": risk_level,
            "dominant_disease": dominant_disease,
            "disease_breakdown": item["diseases"],
            "top_signal_title": item["top_signal_title"],
            "top_signal_url": item["top_signal_url"],
            "top_signal_score": item["top_signal_score"],
        })

    rows.sort(key=lambda x: (-x.get(metric, x["signal_count"]), -x["signal_count"], x["province_name"]))

    total_signals = sum(r["signal_count"] for r in rows)
    high_risk_total = sum(r["high_risk_count"] for r in rows)
    mapped_province_total = len(rows)
    avg_score_national = round(sum((r["avg_score"] * r["signal_count"]) for r in rows) / total_signals, 2) if total_signals else 0

    disease_choices = (
        Signal.objects.exclude(status="noise")
        .exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .values_list("disease_tag", flat=True)
        .distinct()
        .order_by("disease_tag")
    )

    return render(request, "intel/disease_choropleth_map.html", {
        "page_title": "Disease Choropleth Map",
        "metric": metric,
        "disease": disease,
        "days": days,
        "date_from": date_from_obj.isoformat(),
        "date_to": date_to_obj.isoformat(),
        "status_filter": status_filter,
        "status_label": status_label,
        "disease_choices": disease_choices,
        "rows": rows,
        "rows_json": json.dumps(rows, default=str),
        "total_signals": total_signals,
        "high_risk_total": high_risk_total,
        "mapped_province_total": mapped_province_total,
        "avg_score_national": avg_score_national,
    })

# ============================================================
# DECISION SUPPORT / POLICY RECOMMENDATION CENTER
# ============================================================

def _decision_status_config(status_filter):
    """Status filter khusus Decision Support agar tidak mengganggu report existing."""
    if status_filter == "approved":
        return ["approved"], "Approved Mapping"
    if status_filter == "validated":
        return ["validated", "approved"], "Validated + Approved"
    if status_filter == "raw":
        return ["raw"], "Raw Crawling"
    return ["raw", "validated", "approved"], "Data Operasional Crawling"


def _decision_risk_label(avg_score, high_risk_total=0, total=0):
    avg_score = avg_score or 0
    high_risk_total = high_risk_total or 0
    total = total or 0

    if high_risk_total >= 3 or avg_score >= 70:
        return "Tinggi"
    if high_risk_total >= 1 or avg_score >= 55:
        return "Sedang-Tinggi"
    if total >= 3 or avg_score >= 35:
        return "Sedang"
    return "Rendah"


def _decision_priority_score(total, avg_score, high_risk_total):
    total = total or 0
    avg_score = avg_score or 0
    high_risk_total = high_risk_total or 0
    return round((total * 2.0) + avg_score + (high_risk_total * 8.0), 2)


def _decision_action_package(risk_label, disease_name, location_name, total, avg_score, high_risk_total):
    disease_name = disease_name or "penyakit terkait"
    location_name = location_name or "wilayah terkait"

    if risk_label == "Tinggi":
        return {
            "priority": "Tinggi",
            "judgement": (
                f"{disease_name} di {location_name} menjadi prioritas karena terdapat {total} signal, "
                f"{high_risk_total} signal high-risk, dan rata-rata skor {avg_score}."
            ),
            "impact": (
                "Kondisi ini dapat mengindikasikan peningkatan ancaman kesehatan masyarakat, peningkatan atensi publik, "
                "atau kebutuhan verifikasi cepat di wilayah terkait."
            ),
            "action": (
                "Prioritaskan verifikasi 5W+1H, validasi lokasi, pengecekan silang dengan kanal resmi kesehatan, "
                "dan koordinasi cepat dengan pemangku kepentingan wilayah."
            ),
            "owner": "Analis senior / pengambil kebijakan kesehatan",
            "timeline": "0–24 jam",
        }

    if risk_label == "Sedang-Tinggi":
        return {
            "priority": "Sedang-Tinggi",
            "judgement": (
                f"{disease_name} di {location_name} perlu dipantau intensif karena mencatat {total} signal "
                f"dengan rata-rata skor {avg_score} dan {high_risk_total} signal high-risk."
            ),
            "impact": (
                "Signal berpotensi berkembang apabila muncul laporan tambahan dari sumber lain atau wilayah sekitar."
            ),
            "action": (
                "Lakukan monitoring harian, lengkapi assessment pada signal prioritas, dan bandingkan dengan data resmi "
                "apabila tersedia."
            ),
            "owner": "Analis / validator data",
            "timeline": "1–3 hari",
        }

    if risk_label == "Sedang":
        return {
            "priority": "Sedang",
            "judgement": (
                f"{disease_name} di {location_name} terpantau dalam {total} signal dan masih relevan sebagai bahan kewaspadaan dini."
            ),
            "impact": (
                "Belum menunjukkan tekanan tinggi, tetapi dapat menjadi indikator awal jika tren meningkat."
            ),
            "action": (
                "Lanjutkan pemantauan berkala, validasi signal baru, dan perbarui scoring bila terdapat data tambahan."
            ),
            "owner": "Analis monitoring",
            "timeline": "3–7 hari",
        }

    return {
        "priority": "Rendah",
        "judgement": (
            f"{disease_name} di {location_name} belum menunjukkan indikasi menonjol pada periode yang dipilih."
        ),
        "impact": "Dampak operasional masih rendah dan cukup dimonitor melalui mekanisme rutin.",
        "action": "Pertahankan crawling berkala dan lakukan validasi bila muncul peningkatan signal.",
        "owner": "Monitoring rutin",
        "timeline": "Mingguan",
    }


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def decision_support(request):
    """
    Decision Support Center.

    Tujuan:
    - Mengubah data OSINT menjadi rekomendasi tindakan untuk analis/pengambil kebijakan.
    - Tidak menambah model/migrasi.
    - Basis: Signal, SignalLocation, status, disease, lokasi, score, high-risk.
    """
    try:
        days = int(request.GET.get("days", "7"))
    except ValueError:
        days = 7
    if days not in [7, 14, 30, 60, 90]:
        days = 7

    selected_disease = (request.GET.get("disease") or "").strip()
    status_filter = (request.GET.get("status_filter") or "operational").strip()
    province_id = (request.GET.get("province") or "").strip()
    city_id = (request.GET.get("city") or "").strip()
    selected_statuses, status_label = _decision_status_config(status_filter)

    date_to_raw = (request.GET.get("date_to") or "").strip()
    if date_to_raw:
        try:
            end_date = timezone.datetime.fromisoformat(date_to_raw).date()
        except Exception:
            end_date = timezone.now().date()
    else:
        end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days - 1)

    base_qs = (
        Signal.objects.filter(
            published_at__date__gte=start_date,
            published_at__date__lte=end_date,
            status__in=selected_statuses,
        )
        .exclude(status="noise")
        .select_related("source")
    )

    if selected_disease:
        base_qs = base_qs.filter(disease_tag__iexact=selected_disease)

    if city_id:
        base_qs = base_qs.filter(
            locations__is_primary=True,
            locations__location_id=city_id,
        ).distinct()
    elif province_id:
        base_qs = base_qs.filter(
            Q(locations__is_primary=True, locations__location_id=province_id, locations__location__level="province")
            | Q(locations__is_primary=True, locations__location__parent_id=province_id)
        ).distinct()

    total_signals = base_qs.count()
    high_risk_count = base_qs.filter(threat_score__gte=70).count()
    avg_score = round(base_qs.aggregate(avg=Avg("threat_score"))["avg"] or 0, 2)

    disease_rows = list(
        base_qs.exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .values("disease_tag")
        .annotate(
            total=Count("id", distinct=True),
            avg_score=Avg("threat_score"),
            high_risk_total=Count("id", filter=Q(threat_score__gte=70), distinct=True),
        )
        .order_by("-total", "-avg_score", "disease_tag")[:12]
    )

    location_links = (
        SignalLocation.objects.filter(signal__in=base_qs, is_primary=True, location__isnull=False)
        .select_related("signal", "location", "location__parent")
    )

    grouped = {}
    for link in location_links:
        signal = link.signal
        loc = link.location
        if not loc:
            continue

        disease_name = signal.disease_tag or "Tidak diketahui"
        loc_name = loc.display_name or loc.name or "-"
        parent_name = loc.parent.display_name or loc.parent.name if loc.parent else ""
        full_location = f"{loc_name}, {parent_name}" if parent_name and parent_name != loc_name else loc_name
        group_key = f"{disease_name.lower()}::{loc.id}"

        if group_key not in grouped:
            grouped[group_key] = {
                "disease": disease_name,
                "location_id": loc.id,
                "location_name": full_location,
                "level": loc.level or "-",
                "total": 0,
                "score_sum": 0.0,
                "high_risk_total": 0,
                "signals": [],
            }

        item = grouped[group_key]
        score = signal.threat_score or 0
        item["total"] += 1
        item["score_sum"] += score
        if score >= 70:
            item["high_risk_total"] += 1
        if len(item["signals"]) < 4:
            item["signals"].append({
                "id": signal.id,
                "title": signal.title or "-",
                "score": score,
                "published_at": signal.published_at.strftime("%Y-%m-%d") if signal.published_at else "-",
                "source": signal.source.name if signal.source else "-",
                "url": signal.source_url or "",
            })

    recommendation_rows = []
    for item in grouped.values():
        total = item["total"] or 0
        avg_item_score = round(item["score_sum"] / total, 2) if total else 0
        high_item = item["high_risk_total"] or 0
        risk_label = _decision_risk_label(avg_item_score, high_item, total)
        action_package = _decision_action_package(
            risk_label,
            item["disease"],
            item["location_name"],
            total,
            avg_item_score,
            high_item,
        )
        recommendation_rows.append({
            "disease": item["disease"],
            "location_name": item["location_name"],
            "level": item["level"],
            "total": total,
            "avg_score": avg_item_score,
            "high_risk_total": high_item,
            "risk_label": risk_label,
            "priority_score": _decision_priority_score(total, avg_item_score, high_item),
            "signals": item["signals"],
            **action_package,
        })

    recommendation_rows.sort(key=lambda x: (-x["priority_score"], -x["total"], x["disease"], x["location_name"]))
    recommendation_rows = recommendation_rows[:20]

    if recommendation_rows:
        executive_summary = (
            f"Pada periode {start_date.strftime('%d/%m/%Y')} sampai {end_date.strftime('%d/%m/%Y')}, "
            f"sistem mengidentifikasi {len(recommendation_rows)} prioritas rekomendasi dari {total_signals} signal. "
            f"Terdapat {high_risk_count} signal high-risk dengan rata-rata skor {avg_score}. "
            f"Prioritas tertinggi adalah {recommendation_rows[0]['disease']} di {recommendation_rows[0]['location_name']}."
        )
    else:
        executive_summary = (
            f"Belum terdapat rekomendasi spesifik pada periode {start_date.strftime('%d/%m/%Y')} sampai {end_date.strftime('%d/%m/%Y')}. "
            "Coba gunakan status Data Operasional Crawling, perluas rentang tanggal, atau pilih wilayah/penyakit lain."
        )

    provinces = Location.objects.filter(
        level="province", is_active=True, is_false_positive=False
    ).order_by("display_name", "name")
    cities = Location.objects.filter(
        level__in=["city", "regency"], is_active=True, is_false_positive=False
    ).select_related("parent").order_by("display_name", "name")
    if province_id:
        cities = cities.filter(parent_id=province_id)

    disease_choices = (
        Signal.objects.exclude(status="noise")
        .exclude(disease_tag="")
        .exclude(disease_tag__isnull=True)
        .values_list("disease_tag", flat=True)
        .distinct()
        .order_by("disease_tag")
    )

    priority_chart = {
        "labels": [f"{row['disease']} - {row['location_name']}" for row in recommendation_rows[:10]],
        "scores": [row["priority_score"] for row in recommendation_rows[:10]],
        "totals": [row["total"] for row in recommendation_rows[:10]],
    }

    context = {
        "page_title": "Decision Support Center",
        "days": days,
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "status_filter": status_filter,
        "status_label": status_label,
        "selected_disease": selected_disease,
        "province_id": province_id,
        "city_id": city_id,
        "disease_choices": disease_choices,
        "provinces": provinces,
        "cities": cities,
        "total_signals": total_signals,
        "high_risk_count": high_risk_count,
        "avg_score": avg_score,
        "disease_rows": disease_rows,
        "recommendation_rows": recommendation_rows,
        "executive_summary": executive_summary,
        "priority_chart_json": json.dumps(priority_chart, default=str),
    }
    return render(request, "intel/decision_support.html", context)
