from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.sources.models import Source

from ..models import CollectionJob, CollectionJobLog, CollectionSession


QUEUEABLE_STATUSES = (
    CollectionJob.Status.PENDING,
    CollectionJob.Status.RETRY_WAITING,
)


def queue_key_for(*, source_id, job_type: str) -> str:
    scope = f"source:{source_id}" if source_id else "global"
    return f"{scope}:{job_type}"


def log_collection_job(
    job: CollectionJob,
    *,
    event: str,
    message: str,
    level: str = CollectionJobLog.Level.INFO,
    metadata: dict | None = None,
) -> CollectionJobLog:
    return CollectionJobLog.objects.create(
        job=job,
        event=event,
        message=message,
        level=level,
        metadata=metadata or {},
    )


def enqueue_collection_job(
    *,
    source: Source | None,
    job_type: str,
    crawler_name: str,
    triggered_by=None,
    trigger_type: str = "system",
    session: CollectionSession | None = None,
    metadata: dict | None = None,
    max_attempts: int = 3,
    rerun_of: CollectionJob | None = None,
) -> CollectionJob:
    max_attempts = min(max(int(max_attempts), 1), 10)
    queue_key = queue_key_for(
        source_id=source.pk if source else None,
        job_type=job_type,
    )
    job = CollectionJob.objects.create(
        source=source,
        session=session,
        job_type=job_type,
        crawler_name=crawler_name,
        status=CollectionJob.Status.PENDING,
        queue_key=queue_key,
        available_at=timezone.now(),
        max_attempts=max_attempts,
        triggered_by=triggered_by,
        trigger_type=trigger_type,
        rerun_of=rerun_of,
        metadata=metadata or {},
    )
    log_collection_job(
        job,
        event="queued",
        message="Proses masuk antrean persisten dan menunggu worker.",
        metadata={"queue_key": queue_key},
    )
    return job


def _supports_skip_locked() -> bool:
    return bool(connection.features.has_select_for_update_skip_locked)


def claim_next_collection_job(
    *,
    worker_id: str,
    lease_seconds: int | None = None,
) -> CollectionJob | None:
    lease_seconds = lease_seconds or int(
        getattr(settings, "COLLECTION_JOB_LEASE_SECONDS", 180)
    )
    lease_seconds = min(max(lease_seconds, 30), 3600)
    now = timezone.now()

    with transaction.atomic():
        candidates = CollectionJob.objects.filter(
            status__in=QUEUEABLE_STATUSES,
            available_at__lte=now,
            pause_requested_at__isnull=True,
            cancel_requested_at__isnull=True,
        ).order_by("available_at", "created_at")
        if _supports_skip_locked():
            candidates = candidates.select_for_update(skip_locked=True)
        else:
            candidates = candidates.select_for_update()

        for candidate in candidates[:25]:
            if candidate.queue_key and CollectionJob.objects.filter(
                queue_key=candidate.queue_key,
                status=CollectionJob.Status.RUNNING,
            ).exclude(pk=candidate.pk).exists():
                continue

            try:
                with transaction.atomic():
                    updated = CollectionJob.objects.filter(
                        pk=candidate.pk,
                        status__in=QUEUEABLE_STATUSES,
                    ).update(
                        status=CollectionJob.Status.RUNNING,
                        started_at=now,
                        finished_at=None,
                        last_attempt_at=now,
                        attempt_count=F("attempt_count") + 1,
                        heartbeat_at=now,
                        lease_expires_at=now + timedelta(seconds=lease_seconds),
                        worker_id=worker_id,
                        error_message="",
                        updated_at=now,
                    )
            except IntegrityError:
                continue

            if not updated:
                continue

            claimed = CollectionJob.objects.select_related(
                "source", "session", "triggered_by"
            ).get(pk=candidate.pk)
            metadata = {**claimed.metadata, "lease_seconds": lease_seconds}
            CollectionJob.objects.filter(pk=claimed.pk).update(metadata=metadata)
            claimed.metadata = metadata
            log_collection_job(
                claimed,
                event="claimed",
                message=(
                    f"Diklaim worker {worker_id}; percobaan "
                    f"{claimed.attempt_count}/{claimed.max_attempts}."
                ),
                metadata={"worker_id": worker_id},
            )
            return claimed

    return None


def touch_collection_job(
    *,
    job_id,
    worker_id: str = "",
    totals: dict | None = None,
    lease_seconds: int | None = None,
) -> bool:
    now = timezone.now()
    lease_seconds = lease_seconds or int(
        getattr(settings, "COLLECTION_JOB_LEASE_SECONDS", 180)
    )
    lease_seconds = min(max(int(lease_seconds), 30), 3600)
    updates = {
        "heartbeat_at": now,
        "lease_expires_at": now + timedelta(seconds=lease_seconds),
        "updated_at": now,
    }
    for field in (
        "total_found",
        "total_created",
        "total_duplicate",
        "total_rejected",
        "total_failed",
    ):
        if totals and field in totals:
            updates[field] = max(int(totals[field]), 0)

    query = CollectionJob.objects.filter(
        pk=job_id,
        status=CollectionJob.Status.RUNNING,
    )
    if worker_id:
        query = query.filter(worker_id=worker_id)
    return bool(query.update(**updates))


@transaction.atomic
def request_collection_job_pause(
    *,
    job: CollectionJob,
    actor=None,
) -> CollectionJob:
    locked = CollectionJob.objects.select_for_update().get(pk=job.pk)
    now = timezone.now()
    if locked.status == CollectionJob.Status.RUNNING:
        locked.pause_requested_at = now
        locked.save(update_fields=["pause_requested_at", "updated_at"])
        message = "Permintaan jeda dikirim; worker akan berhenti pada checkpoint aman."
    elif locked.status in QUEUEABLE_STATUSES:
        locked.status = CollectionJob.Status.PAUSED
        locked.pause_requested_at = now
        locked.worker_id = ""
        locked.lease_expires_at = None
        locked.save(
            update_fields=[
                "status",
                "pause_requested_at",
                "worker_id",
                "lease_expires_at",
                "updated_at",
            ]
        )
        message = "Proses dijeda sebelum diklaim worker."
    else:
        raise ValidationError("Hanya proses menunggu atau berjalan yang dapat dijeda.")
    log_collection_job(
        locked,
        event="pause_requested",
        message=message,
        metadata={"actor": getattr(actor, "get_username", lambda: "")()},
    )
    return locked


@transaction.atomic
def resume_collection_job(*, job: CollectionJob, actor=None) -> CollectionJob:
    locked = CollectionJob.objects.select_for_update().get(pk=job.pk)
    if locked.status != CollectionJob.Status.PAUSED:
        raise ValidationError("Hanya proses berstatus Dijeda yang dapat dilanjutkan.")
    locked.status = CollectionJob.Status.PENDING
    locked.available_at = timezone.now()
    locked.pause_requested_at = None
    locked.cancel_requested_at = None
    locked.worker_id = ""
    locked.lease_expires_at = None
    locked.finished_at = None
    locked.save(
        update_fields=[
            "status",
            "available_at",
            "pause_requested_at",
            "cancel_requested_at",
            "worker_id",
            "lease_expires_at",
            "finished_at",
            "updated_at",
        ]
    )
    log_collection_job(
        locked,
        event="resumed",
        message="Proses dikembalikan ke antrean untuk dilanjutkan worker.",
        metadata={"actor": getattr(actor, "get_username", lambda: "")()},
    )
    return locked


@transaction.atomic
def request_collection_job_cancel(
    *,
    job: CollectionJob,
    actor=None,
) -> CollectionJob:
    locked = CollectionJob.objects.select_for_update().get(pk=job.pk)
    now = timezone.now()
    if locked.status == CollectionJob.Status.RUNNING:
        locked.cancel_requested_at = now
        locked.save(update_fields=["cancel_requested_at", "updated_at"])
        message = "Permintaan pembatalan dikirim ke worker."
    elif locked.status in (*QUEUEABLE_STATUSES, CollectionJob.Status.PAUSED):
        locked.status = CollectionJob.Status.CANCELLED
        locked.cancel_requested_at = now
        locked.finished_at = now
        locked.worker_id = ""
        locked.lease_expires_at = None
        locked.save(
            update_fields=[
                "status",
                "cancel_requested_at",
                "finished_at",
                "worker_id",
                "lease_expires_at",
                "updated_at",
            ]
        )
        message = "Proses dibatalkan sebelum selesai dieksekusi."
    else:
        raise ValidationError("Proses ini sudah selesai dan tidak dapat dibatalkan.")
    log_collection_job(
        locked,
        event="cancel_requested",
        message=message,
        metadata={"actor": getattr(actor, "get_username", lambda: "")()},
    )
    return locked


@transaction.atomic
def mark_collection_job_interrupted(
    *,
    job: CollectionJob,
    status: str,
    totals: dict,
    message: str,
) -> CollectionJob:
    if status not in {CollectionJob.Status.PAUSED, CollectionJob.Status.CANCELLED}:
        raise ValueError("Status interupsi job tidak valid.")
    now = timezone.now()
    locked = CollectionJob.objects.select_for_update().get(pk=job.pk)
    locked.status = status
    locked.finished_at = now if status == CollectionJob.Status.CANCELLED else None
    locked.worker_id = ""
    locked.lease_expires_at = None
    for field, value in totals.items():
        if hasattr(locked, field):
            setattr(locked, field, max(int(value), 0))
    locked.save(
        update_fields=[
            "status",
            "finished_at",
            "worker_id",
            "lease_expires_at",
            "total_found",
            "total_created",
            "total_duplicate",
            "total_rejected",
            "total_failed",
            "updated_at",
        ]
    )
    log_collection_job(
        locked,
        event="paused" if status == CollectionJob.Status.PAUSED else "cancelled",
        message=message,
        level=CollectionJobLog.Level.WARNING,
    )
    return locked


def _retry_delay_seconds(attempt_count: int) -> int:
    base = int(getattr(settings, "COLLECTION_RETRY_BASE_SECONDS", 60))
    maximum = int(getattr(settings, "COLLECTION_RETRY_MAX_SECONDS", 900))
    return min(maximum, max(base, 1) * (2 ** max(attempt_count - 1, 0)))


@transaction.atomic
def schedule_collection_job_retry(
    *,
    job: CollectionJob,
    error_message: str,
) -> bool:
    locked = CollectionJob.objects.select_for_update().get(pk=job.pk)
    if locked.attempt_count >= locked.max_attempts:
        return False
    delay_seconds = _retry_delay_seconds(locked.attempt_count)
    locked.status = CollectionJob.Status.RETRY_WAITING
    locked.available_at = timezone.now() + timedelta(seconds=delay_seconds)
    locked.finished_at = None
    locked.worker_id = ""
    locked.lease_expires_at = None
    locked.error_message = error_message
    locked.save(
        update_fields=[
            "status",
            "available_at",
            "finished_at",
            "worker_id",
            "lease_expires_at",
            "error_message",
            "updated_at",
        ]
    )
    log_collection_job(
        locked,
        event="retry_scheduled",
        message=(
            f"Kegagalan sementara; percobaan berikutnya dijadwalkan "
            f"dalam {delay_seconds} detik."
        ),
        level=CollectionJobLog.Level.WARNING,
        metadata={"error": error_message, "delay_seconds": delay_seconds},
    )
    return True


@transaction.atomic
def rerun_collection_job(*, job: CollectionJob, actor=None) -> CollectionJob:
    original = CollectionJob.objects.select_for_update(
        of=("self",)
    ).select_related(
        "source", "session"
    ).get(pk=job.pk)
    if original.status in {
        CollectionJob.Status.PENDING,
        CollectionJob.Status.RUNNING,
        CollectionJob.Status.RETRY_WAITING,
        CollectionJob.Status.PAUSED,
    }:
        raise ValidationError(
            "Proses yang masih aktif harus dijeda/dibatalkan atau dilanjutkan, "
            "bukan dijalankan ulang."
        )
    active_exists = CollectionJob.objects.filter(
        queue_key=original.queue_key,
        status__in=(
            CollectionJob.Status.PENDING,
            CollectionJob.Status.RUNNING,
            CollectionJob.Status.RETRY_WAITING,
        ),
    ).exclude(pk=original.pk).exists()
    if active_exists:
        raise ValidationError("Sumber dan kanal yang sama masih berada dalam antrean.")
    rerun = enqueue_collection_job(
        source=original.source,
        session=original.session,
        job_type=original.job_type,
        crawler_name=original.crawler_name,
        triggered_by=actor,
        trigger_type="rerun",
        metadata={**original.metadata, "rerun_of": str(original.id)},
        max_attempts=original.max_attempts,
        rerun_of=original,
    )
    if original.session_id:
        CollectionSession.objects.filter(pk=original.session_id).update(
            planned_job_count=F("planned_job_count") + 1
        )
    log_collection_job(
        original,
        event="rerun_created",
        message=f"Proses baru {rerun.id} dibuat sebagai jalankan ulang.",
        metadata={"rerun_job_id": str(rerun.id)},
    )
    return rerun


def recover_stale_collection_jobs(*, now=None) -> dict[str, int]:
    now = now or timezone.now()
    recovered = failed = paused = cancelled = 0
    stale_ids = list(
        CollectionJob.objects.filter(
            status=CollectionJob.Status.RUNNING,
        ).filter(
            Q(lease_expires_at__lt=now)
            | Q(lease_expires_at__isnull=True, heartbeat_at__lt=now - timedelta(minutes=30))
        ).values_list("id", flat=True)
    )
    for job_id in stale_ids:
        with transaction.atomic():
            job = CollectionJob.objects.select_for_update().get(pk=job_id)
            if job.status != CollectionJob.Status.RUNNING:
                continue
            if job.cancel_requested_at:
                mark_collection_job_interrupted(
                    job=job,
                    status=CollectionJob.Status.CANCELLED,
                    totals={},
                    message="Dibatalkan saat pemulihan lease worker yang kedaluwarsa.",
                )
                cancelled += 1
            elif job.pause_requested_at:
                mark_collection_job_interrupted(
                    job=job,
                    status=CollectionJob.Status.PAUSED,
                    totals={},
                    message="Dijeda saat pemulihan lease worker yang kedaluwarsa.",
                )
                paused += 1
            elif schedule_collection_job_retry(
                job=job,
                error_message="Worker terputus atau heartbeat kedaluwarsa.",
            ):
                recovered += 1
            else:
                job.status = CollectionJob.Status.FAILED
                job.finished_at = now
                job.worker_id = ""
                job.lease_expires_at = None
                job.error_message = "Worker terputus dan batas percobaan telah tercapai."
                job.total_failed = max(job.total_failed, 1)
                job.save(
                    update_fields=[
                        "status",
                        "finished_at",
                        "worker_id",
                        "lease_expires_at",
                        "error_message",
                        "total_failed",
                        "updated_at",
                    ]
                )
                log_collection_job(
                    job,
                    event="recovery_failed",
                    message=job.error_message,
                    level=CollectionJobLog.Level.ERROR,
                )
                failed += 1
    return {
        "recovered": recovered,
        "failed": failed,
        "paused": paused,
        "cancelled": cancelled,
    }
