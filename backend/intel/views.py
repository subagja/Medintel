import csv
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User, Group
from .permissions import role_required, user_has_role, ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count, Avg, Min, Max
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
    SignalLocation,
    AuditLog,
    Location,
    LocationAlias,
    ScoringRule,
    SystemSetting,
    Alert,
)

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
    report_type = request.GET.get("report_type", "daily").strip()

    now = timezone.now()

    if not date_to:
        date_to_obj = now.date()
    else:
        date_to_obj = timezone.datetime.fromisoformat(date_to).date()

    if not date_from:
        if report_type == "weekly":
            date_from_obj = date_to_obj - timedelta(days=6)
        else:
            date_from_obj = date_to_obj
    else:
        date_from_obj = timezone.datetime.fromisoformat(date_from).date()

    return report_type, date_from_obj, date_to_obj

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def reports_generator(request):
    report_type, date_from_obj, date_to_obj = get_date_range_from_request(request)

    base_qs = Signal.objects.filter(
        published_at__date__gte=date_from_obj,
        published_at__date__lte=date_to_obj,
        status__in=["validated", "approved"],
    ).select_related("source", "validated_by")

    total_signals = base_qs.count()
    high_risk_count = base_qs.filter(threat_score__gt=50).count()
    geocode_ok_count = base_qs.filter(geocode_status="OK").count()

    high_risk_rate = round((high_risk_count / total_signals) * 100, 2) if total_signals else 0
    geocode_success_rate = round((geocode_ok_count / total_signals) * 100, 2) if total_signals else 0
    avg_score = round(base_qs.aggregate(avg_score=Avg("threat_score"))["avg_score"] or 0, 2)

    disease_recap = (
        base_qs.exclude(disease_tag="")
        .values("disease_tag")
        .annotate(total=Count("id"), avg_score=Avg("threat_score"))
        .order_by("-total", "disease_tag")
    )

    province_recap = (
        SignalLocation.objects.filter(
            signal__in=base_qs,
            is_primary=True,
            location__isnull=False,
        )
        .values("location__province_code")
        .annotate(total=Count("id"))
        .order_by("-total", "location__province_code")
    )

    location_recap = (
        SignalLocation.objects.filter(
            signal__in=base_qs,
            is_primary=True,
            location__isnull=False,
        )
        .values("location__display_name", "location__level")
        .annotate(total=Count("id"))
        .order_by("-total", "location__display_name")[:20]
    )

    recent_signals = base_qs.order_by("-published_at", "-created_at")[:50]

    context = {
        "page_title": "Reports Generator",
        "report_type": report_type,
        "date_from": date_from_obj.isoformat(),
        "date_to": date_to_obj.isoformat(),
        "total_signals": total_signals,
        "high_risk_count": high_risk_count,
        "geocode_ok_count": geocode_ok_count,
        "high_risk_rate": high_risk_rate,
        "geocode_success_rate": geocode_success_rate,
        "avg_score": avg_score,
        "disease_recap": disease_recap,
        "province_recap": province_recap,
        "location_recap": location_recap,
        "recent_signals": recent_signals,
    }
    return render(request, "intel/reports_generator.html", context)

@login_required
def export_signals_csv(request):
    report_type, date_from_obj, date_to_obj = get_date_range_from_request(request)

    qs = Signal.objects.filter(
        published_at__date__gte=date_from_obj,
        published_at__date__lte=date_to_obj,
        status__in=["validated", "approved"],
    ).select_related("source", "validated_by").order_by("-published_at", "-created_at")

    response = HttpResponse(content_type="text/csv")
    filename = f"signals_{report_type}_{date_from_obj.isoformat()}_{date_to_obj.isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        "id",
        "title",
        "source",
        "source_url",
        "published_at",
        "disease_tag",
        "threat_score",
        "raw_location_text",
        "geocode_status",
        "status",
        "approved_for_mapping",
        "is_high_risk",
        "validated_by",
        "validated_at",
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
            str(signal.validated_by) if signal.validated_by else "",
            signal.validated_at.isoformat() if signal.validated_at else "",
        ])

    return response

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def raw_signals_list(request):
    qs = Signal.objects.filter(status="raw").select_related("source", "validated_by")

    q = request.GET.get("q", "").strip()
    disease = request.GET.get("disease", "").strip()
    geocode_status = request.GET.get("geocode_status", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    sort = request.GET.get("sort", "-published_at").strip()

    if q:
        qs = qs.filter(
            Q(title__icontains=q) |
            Q(source_url__icontains=q) |
            Q(raw_location_text__icontains=q)
        )

    if disease:
        qs = qs.filter(disease_tag__iexact=disease)

    if geocode_status:
        qs = qs.filter(geocode_status=geocode_status)

    if date_from:
        qs = qs.filter(published_at__date__gte=date_from)

    if date_to:
        qs = qs.filter(published_at__date__lte=date_to)

    allowed_sort = {
        "published_at": "published_at",
        "-published_at": "-published_at",
        "threat_score": "threat_score",
        "-threat_score": "-threat_score",
        "title": "title",
        "-title": "-title",
    }
    qs = qs.order_by(allowed_sort.get(sort, "-published_at"), "-created_at")

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    disease_choices = (
        Signal.objects.exclude(disease_tag="")
        .values_list("disease_tag", flat=True)
        .distinct()
        .order_by("disease_tag")
    )

    geocode_choices = (
        Signal.objects.values_list("geocode_status", flat=True)
        .distinct()
        .order_by("geocode_status")
    )

    context = {
        "page_obj": page_obj,
        "q": q,
        "disease": disease,
        "geocode_status": geocode_status,
        "date_from": date_from,
        "date_to": date_to,
        "sort": sort,
        "disease_choices": disease_choices,
        "geocode_choices": geocode_choices,
        "page_title": "Raw Signals",
    }
    return render(request, "intel/raw_signals_list.html", context)


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def verified_signals_list(request):
    qs = Signal.objects.filter(status__in=["validated", "approved"]).select_related("source", "validated_by")

    q = request.GET.get("q", "").strip()
    disease = request.GET.get("disease", "").strip()
    status_filter = request.GET.get("status", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    sort = request.GET.get("sort", "-published_at").strip()

    if q:
        qs = qs.filter(
            Q(title__icontains=q) |
            Q(source_url__icontains=q) |
            Q(raw_location_text__icontains=q)
        )

    if disease:
        qs = qs.filter(disease_tag__iexact=disease)

    if status_filter:
        qs = qs.filter(status=status_filter)

    if date_from:
        qs = qs.filter(published_at__date__gte=date_from)

    if date_to:
        qs = qs.filter(published_at__date__lte=date_to)

    allowed_sort = {
        "published_at": "published_at",
        "-published_at": "-published_at",
        "threat_score": "threat_score",
        "-threat_score": "-threat_score",
        "title": "title",
        "-title": "-title",
    }
    qs = qs.order_by(allowed_sort.get(sort, "-published_at"), "-created_at")

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    disease_choices = (
        Signal.objects.exclude(disease_tag="")
        .values_list("disease_tag", flat=True)
        .distinct()
        .order_by("disease_tag")
    )

    context = {
        "page_obj": page_obj,
        "q": q,
        "disease": disease,
        "status_filter": status_filter,
        "date_from": date_from,
        "date_to": date_to,
        "sort": sort,
        "disease_choices": disease_choices,
        "page_title": "Verified Signals",
    }
    return render(request, "intel/verified_signals_list.html", context)


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def signal_mark_validated(request, pk):
    signal = get_object_or_404(Signal, pk=pk)
    signal.status = "validated"
    signal.validated_by = request.user
    signal.validated_at = timezone.now()
    signal.save()
    messages.success(request, f'Signal "{signal.title[:60]}" ditandai sebagai validated.')
    return redirect(request.META.get("HTTP_REFERER", "intel:raw_signals"))


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def signal_mark_noise(request, pk):
    signal = get_object_or_404(Signal, pk=pk)
    signal.status = "noise"
    signal.validated_by = request.user
    signal.validated_at = timezone.now()
    signal.save()
    messages.warning(request, f'Signal "{signal.title[:60]}" ditandai sebagai noise.')
    return redirect(request.META.get("HTTP_REFERER", "intel:raw_signals"))


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def signal_approve_mapping(request, pk):
    signal = get_object_or_404(Signal, pk=pk)
    signal.status = "approved"
    signal.approved_for_mapping = True
    signal.validated_by = request.user
    signal.validated_at = timezone.now()
    signal.save()
    messages.success(request, f'Signal "{signal.title[:60]}" di-approve untuk mapping.')
    return redirect(request.META.get("HTTP_REFERER", "intel:verified_signals"))


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def signal_quick_score(request, pk):
    signal = get_object_or_404(Signal, pk=pk)

    if request.method == "POST":
        try:
            new_score = int(request.POST.get("threat_score", signal.threat_score))
        except ValueError:
            messages.error(request, "Nilai threat score tidak valid.")
            return redirect(request.META.get("HTTP_REFERER", "intel:raw_signals"))

        signal.threat_score = new_score
        signal.validated_by = request.user
        signal.validated_at = timezone.now()
        signal.save()

        messages.success(request, f'Skor signal "{signal.title[:60]}" berhasil diperbarui.')
        return redirect(request.META.get("HTTP_REFERER", "intel:raw_signals"))

    return redirect("intel:raw_signals")

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
        "EMPTY_LOC",
        "NOT_FOUND",
        "NET_ERR",
        "SKIP_NOISE",
        "SKIP_TOO_GENERAL",
        "SKIP_LOW_CONF",
    ]

    qs = Signal.objects.filter(geocode_status__in=error_statuses).select_related("source", "validated_by")

    q = request.GET.get("q", "").strip()
    geocode_status = request.GET.get("geocode_status", "").strip()
    disease = request.GET.get("disease", "").strip()
    sort = request.GET.get("sort", "-published_at").strip()

    if q:
        qs = qs.filter(
            Q(title__icontains=q) |
            Q(raw_location_text__icontains=q) |
            Q(source_url__icontains=q)
        )

    if geocode_status:
        qs = qs.filter(geocode_status=geocode_status)

    if disease:
        qs = qs.filter(disease_tag__iexact=disease)

    allowed_sort = {
        "published_at": "published_at",
        "-published_at": "-published_at",
        "threat_score": "threat_score",
        "-threat_score": "-threat_score",
        "title": "title",
        "-title": "-title",
    }
    qs = qs.order_by(allowed_sort.get(sort, "-published_at"), "-created_at")

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    geocode_choices = error_statuses
    disease_choices = (
        Signal.objects.exclude(disease_tag="")
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

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def geocode_manual_update(request, pk):
    signal = get_object_or_404(Signal, pk=pk)

    signal_location = (
        SignalLocation.objects.filter(signal=signal, is_primary=True)
        .select_related("location")
        .first()
    )

    initial_data = {
        "raw_location_text": signal.raw_location_text,
        "geocode_status": signal.geocode_status,
        "location": signal_location.location if signal_location and signal_location.location else None,
        "confidence": signal_location.confidence if signal_location else None,
        "notes": signal.validation_notes,
    }

    if request.method == "POST":
        form = GeocodeManualUpdateForm(request.POST)
        if form.is_valid():
            old_status = signal.geocode_status
            old_raw_location = signal.raw_location_text

            signal.raw_location_text = form.cleaned_data["raw_location_text"]
            signal.geocode_status = form.cleaned_data["geocode_status"]
            signal.validation_notes = form.cleaned_data["notes"]
            signal.validated_by = request.user
            signal.validated_at = timezone.now()
            signal.save()

            location = form.cleaned_data["location"]
            confidence = form.cleaned_data["confidence"]

            if signal_location:
                signal_location.location = location
                signal_location.raw_location_text = signal.raw_location_text
                signal_location.confidence = confidence
                signal_location.method = "manual"
                signal_location.is_primary = True
                signal_location.save()
            else:
                SignalLocation.objects.create(
                    signal=signal,
                    location=location,
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
                },
                after_data={
                    "geocode_status": signal.geocode_status,
                    "raw_location_text": signal.raw_location_text,
                    "location_id": location.id if location else None,
                    "confidence": confidence,
                },
            )

            messages.success(request, f'Geocode signal "{signal.title[:60]}" berhasil diperbarui secara manual.')
            return redirect("intel:geocode_error_center")
    else:
        form = GeocodeManualUpdateForm(initial=initial_data)

    context = {
        "page_title": "Manual Geocode Update",
        "signal": signal,
        "form": form,
        "signal_location": signal_location,
    }
    return render(request, "intel/geocode_manual_update.html", context)

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST)
def geocode_mark_manual_ok(request, pk):
    signal = get_object_or_404(Signal, pk=pk)
    signal.geocode_status = "MANUAL"
    signal.validated_by = request.user
    signal.validated_at = timezone.now()
    signal.save()

    AuditLog.objects.create(
        user=request.user,
        action="manual_edit",
        model_name="Signal",
        object_id=str(signal.id),
        notes="Geocode status set to MANUAL",
        before_data={},
        after_data={"geocode_status": "MANUAL"},
    )

    messages.success(request, f'Signal "{signal.title[:60]}" ditandai sebagai geocode manual.')
    return redirect(request.META.get("HTTP_REFERER", "intel:geocode_error_center"))

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def gazetteer_manager(request):
    qs = Location.objects.select_related("parent").all()

    q = request.GET.get("q", "").strip()
    level = request.GET.get("level", "").strip()
    province = request.GET.get("province", "").strip()
    false_positive = request.GET.get("false_positive", "").strip()
    is_active = request.GET.get("is_active", "").strip()

    if q:
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(display_name__icontains=q) |
            Q(normalized_name__icontains=q) |
            Q(aliases__alias__icontains=q)
        ).distinct()

    if level:
        qs = qs.filter(level=level)

    if province:
        qs = qs.filter(province_code=province)

    if false_positive == "yes":
        qs = qs.filter(is_false_positive=True)
    elif false_positive == "no":
        qs = qs.filter(is_false_positive=False)

    if is_active == "yes":
        qs = qs.filter(is_active=True)
    elif is_active == "no":
        qs = qs.filter(is_active=False)

    qs = qs.order_by("level", "display_name", "name")

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    province_choices = (
        Location.objects.filter(level="province")
        .exclude(province_code="")
        .values_list("province_code", "display_name")
        .distinct()
        .order_by("display_name")
    )

    context = {
        "page_title": "Gazetteer Manager",
        "page_obj": page_obj,
        "q": q,
        "level": level,
        "province": province,
        "false_positive": false_positive,
        "is_active": is_active,
        "province_choices": province_choices,
        "level_choices": Location.LEVEL_CHOICES,
    }
    return render(request, "intel/gazetteer_manager.html", context)


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


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def gazetteer_alias_manager(request):
    qs = LocationAlias.objects.select_related("location").all()

    q = request.GET.get("q", "").strip()
    is_active = request.GET.get("is_active", "").strip()
    is_primary = request.GET.get("is_primary", "").strip()

    if q:
        qs = qs.filter(
            Q(alias__icontains=q) |
            Q(normalized_alias__icontains=q) |
            Q(location__display_name__icontains=q) |
            Q(location__name__icontains=q)
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
            Q(name__icontains=q) |
            Q(keyword__icontains=q) |
            Q(notes__icontains=q)
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

@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def alert_center(request):
    qs = Alert.objects.select_related("location").all()

    status_filter = request.GET.get("status", "").strip()
    alert_type = request.GET.get("alert_type", "").strip()

    if status_filter:
        qs = qs.filter(status=status_filter)

    if alert_type:
        qs = qs.filter(alert_type=alert_type)

    qs = qs.order_by("-created_at")

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "intel/alert_center.html", {
        "page_title": "Alert Center",
        "page_obj": page_obj,
        "status_filter": status_filter,
        "alert_type": alert_type,
        "alert_type_choices": Alert.ALERT_TYPE_CHOICES,
        "status_choices": Alert.STATUS_CHOICES,
    })


@login_required
@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR)
def generate_alerts(request):
    now = timezone.now()

    city_signal_threshold = int(get_system_setting_value("alert_city_signal_count", 5))
    alert_window_hours = int(get_system_setting_value("alert_window_hours", 48))
    avg_score_threshold = float(get_system_setting_value("alert_avg_score_threshold", 60))

    since_time = now - timedelta(hours=alert_window_hours)

    verified_qs = Signal.objects.filter(
        published_at__gte=since_time,
        status__in=["validated", "approved"],
    )

    # 1. Cluster in city within 48h
    city_clusters = (
        SignalLocation.objects.filter(
            signal__in=verified_qs,
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

    created_count = 0

    for item in city_clusters:
        location_id = item["location_id"]
        loc_name = item["location__display_name"] or "Unknown Location"
        dedup_key = f"cluster_city_48h::{location_id}::{since_time.date()}::{item['total']}"

        _, created = Alert.objects.get_or_create(
            dedup_key=dedup_key,
            defaults={
                "alert_type": "cluster_city_48h",
                "title": f"Cluster signal di {loc_name}",
                "description": f"Terdeteksi {item['total']} signal dalam {alert_window_hours} jam terakhir di {loc_name}.",
                "location_id": location_id,
                "signal_count": item["total"],
                "avg_score": round(item["avg_score"] or 0, 2),
                "status": "open",
                "first_signal_at": item["first_signal_at"],
                "last_signal_at": item["last_signal_at"],
                "rule_key": "cluster_city_48h",
            },
        )
        if created:
            created_count += 1

    # 2. High average score in city/regency
    high_avg_groups = (
        SignalLocation.objects.filter(
            signal__in=verified_qs,
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
        dedup_key = f"high_avg_score::{location_id}::{since_time.date()}::{round(item['avg_score'] or 0, 2)}"

        _, created = Alert.objects.get_or_create(
            dedup_key=dedup_key,
            defaults={
                "alert_type": "high_avg_score",
                "title": f"Rata-rata skor tinggi di {loc_name}",
                "description": f"Rata-rata skor {round(item['avg_score'] or 0, 2)} pada {item['total']} signal di {loc_name}.",
                "location_id": location_id,
                "signal_count": item["total"],
                "avg_score": round(item["avg_score"] or 0, 2),
                "status": "open",
                "first_signal_at": item["first_signal_at"],
                "last_signal_at": item["last_signal_at"],
                "rule_key": "high_avg_score",
            },
        )
        if created:
            created_count += 1

    # 3. New location appeared recently
    recent_locations = (
        SignalLocation.objects.filter(
            signal__in=verified_qs,
            is_primary=True,
            location__isnull=False,
        )
        .values("location_id", "location__display_name")
        .annotate(
            first_signal_at=Min("signal__published_at"),
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
            dedup_key = f"new_location::{location_id}::{since_time.date()}"

            _, created = Alert.objects.get_or_create(
                dedup_key=dedup_key,
                defaults={
                    "alert_type": "new_location",
                    "title": f"Lokasi baru muncul: {loc_name}",
                    "description": f"Lokasi {loc_name} muncul sebagai signal terverifikasi dalam {alert_window_hours} jam terakhir.",
                    "location_id": location_id,
                    "signal_count": item["total"],
                    "avg_score": round(item["avg_score"] or 0, 2),
                    "status": "open",
                    "first_signal_at": item["first_signal_at"],
                    "last_signal_at": item["first_signal_at"],
                    "rule_key": "new_location",
                },
            )
            if created:
                created_count += 1

    messages.success(request, f"Generate alert selesai. Alert baru dibuat: {created_count}.")
    return redirect("intel:alert_center")


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

@role_required(ROLE_ADMIN, ROLE_ANALYST_SENIOR, ROLE_ANALYST, ROLE_VIEWER)
def map_intelligence(request):
    disease = request.GET.get("disease", "").strip()
    min_score = request.GET.get("min_score", "35").strip()
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