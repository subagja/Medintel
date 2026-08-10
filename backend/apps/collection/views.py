from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.permissions import Roles, require_role_by_method

from .forms import CollectionScheduleForm
from .models import CollectionJob, CollectionSchedule, CollectionWorker
from .services.scheduling import (
    calculate_next_run_at,
    run_collection_schedule,
)
from .services.worker import active_worker_cutoff


@require_role_by_method(
    read_roles=Roles.ALL,
    write_roles=(Roles.ADMIN, Roles.ANALYST),
)
def collection_schedule_workspace(request):
    form = CollectionScheduleForm()
    if request.method == "POST":
        action = request.POST.get("action", "create")
        if action == "create":
            form = CollectionScheduleForm(request.POST)
            if form.is_valid():
                schedule = form.save(commit=False)
                schedule.created_by = request.user
                schedule.save()
                messages.success(
                    request,
                    f"Jadwal {schedule.name} berhasil dibuat.",
                )
                return redirect("dashboard:crawler-schedule-workspace")
        else:
            schedule = get_object_or_404(
                CollectionSchedule.objects.select_related(
                    "source", "intelligence_requirement"
                ),
                pk=request.POST.get("schedule_id"),
            )
            if action == "toggle":
                schedule.is_active = not schedule.is_active
                if schedule.is_active:
                    schedule.next_run_at = calculate_next_run_at(
                        schedule,
                        after=timezone.now(),
                    )
                schedule.save(update_fields=["is_active", "next_run_at", "updated_at"])
                messages.success(
                    request,
                    "Jadwal diaktifkan." if schedule.is_active else "Jadwal dinonaktifkan.",
                )
            elif action == "run_now":
                try:
                    session = run_collection_schedule(
                        schedule,
                        triggered_by=request.user,
                        trigger_type="schedule_manual",
                    )
                except Exception as exc:
                    schedule.last_run_at = timezone.now()
                    schedule.last_error = str(exc)
                    schedule.save(
                        update_fields=["last_run_at", "last_error", "updated_at"]
                    )
                    messages.error(request, f"Jadwal tidak dapat dijalankan: {exc}")
                else:
                    schedule.last_run_at = timezone.now()
                    schedule.last_session = session
                    schedule.last_error = ""
                    schedule.save(
                        update_fields=[
                            "last_run_at",
                            "last_session",
                            "last_error",
                            "updated_at",
                        ]
                    )
                    messages.success(
                        request,
                        f"{session.reference} masuk antrean dari jadwal ini.",
                    )
            return redirect("dashboard:crawler-schedule-workspace")

    schedules = CollectionSchedule.objects.select_related(
        "source",
        "intelligence_requirement",
        "last_session",
        "created_by",
    )
    active_workers = CollectionWorker.objects.filter(
        status=CollectionWorker.Status.ACTIVE,
        last_heartbeat_at__gte=active_worker_cutoff(),
    )
    queue_summary = CollectionJob.objects.aggregate(
        pending=Count(
            "id",
            filter=Q(
                status__in=(
                    CollectionJob.Status.PENDING,
                    CollectionJob.Status.RETRY_WAITING,
                )
            ),
        ),
        running=Count("id", filter=Q(status=CollectionJob.Status.RUNNING)),
        paused=Count("id", filter=Q(status=CollectionJob.Status.PAUSED)),
    )
    return render(
        request,
        "dashboard/crawler_schedule_workspace.html",
        {
            "page_title": "Penjadwalan Koleksi",
            "active_menu": "crawler-artikel",
            "form": form,
            "schedules": schedules,
            "active_workers": active_workers,
            "queue_summary": queue_summary,
        },
    )
