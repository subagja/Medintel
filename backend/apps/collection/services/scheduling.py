from __future__ import annotations

from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from ..models import CollectionSchedule


def calculate_next_run_at(
    schedule: CollectionSchedule,
    *,
    after=None,
):
    after = after or timezone.now()
    if schedule.recurrence == CollectionSchedule.Recurrence.HOURLY:
        return after + timedelta(hours=1)
    if schedule.recurrence == CollectionSchedule.Recurrence.EVERY_6_HOURS:
        return after + timedelta(hours=6)

    local_after = timezone.localtime(after)
    candidate = datetime.combine(
        local_after.date(),
        schedule.run_time,
        tzinfo=local_after.tzinfo,
    )
    if schedule.recurrence == CollectionSchedule.Recurrence.DAILY:
        if candidate <= local_after:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.get_current_timezone())

    days_ahead = (schedule.weekday - local_after.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= local_after:
        candidate += timedelta(days=7)
    return candidate.astimezone(timezone.get_current_timezone())


def run_collection_schedule(
    schedule: CollectionSchedule,
    *,
    triggered_by=None,
    trigger_type: str = "schedule",
):
    from apps.crawlers.unified import start_unified_collection

    requirement = schedule.intelligence_requirement
    if requirement and (
        not requirement.is_active
        or requirement.status != requirement.Status.ACTIVE
    ):
        requirement = None
    session = start_unified_collection(
        source_code=schedule.source.code,
        include_google_news=schedule.include_google_news,
        html_deep_scan=schedule.html_deep_scan,
        article_limit=schedule.article_limit,
        candidate_limit=schedule.candidate_limit,
        triggered_by=triggered_by or schedule.created_by,
        trigger_type=trigger_type,
        requirement=requirement,
        max_attempts=schedule.max_attempts,
        schedule=schedule,
    )
    return session


def enqueue_due_collection_schedules(*, now=None, limit: int = 20) -> dict:
    now = now or timezone.now()
    scheduled = []
    failed = []
    due_ids = list(
        CollectionSchedule.objects.filter(
            is_active=True,
            next_run_at__lte=now,
        )
        .order_by("next_run_at")
        .values_list("id", flat=True)[: max(int(limit), 1)]
    )
    for schedule_id in due_ids:
        with transaction.atomic():
            schedule = CollectionSchedule.objects.select_for_update(
                of=("self",)
            ).select_related(
                "source", "created_by", "intelligence_requirement"
            ).get(pk=schedule_id)
            if not schedule.is_active or schedule.next_run_at > now:
                continue
            try:
                session = run_collection_schedule(schedule)
            except Exception as exc:
                schedule.last_error = str(exc)
                failed.append({"schedule_id": str(schedule.id), "error": str(exc)})
            else:
                schedule.last_session = session
                schedule.last_error = ""
                scheduled.append(str(session.id))
            schedule.last_run_at = now
            schedule.next_run_at = calculate_next_run_at(schedule, after=now)
            schedule.save(
                update_fields=[
                    "last_session",
                    "last_error",
                    "last_run_at",
                    "next_run_at",
                    "updated_at",
                ]
            )
    return {"scheduled": scheduled, "failed": failed}
