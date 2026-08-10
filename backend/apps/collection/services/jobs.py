from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.sources.models import Source

from ..models import CollectionJob, CollectionSession
from .queue import queue_key_for


@transaction.atomic
def start_collection_job(
    *,
    source: Source | None,
    job_type: str,
    crawler_name: str = "",
    triggered_by=None,
    trigger_type: str = "system",
    metadata: dict | None = None,
    session: CollectionSession | None = None,
    existing_job: CollectionJob | None = None,
) -> CollectionJob:
    if existing_job is not None:
        locked_job = CollectionJob.objects.select_for_update().get(
            pk=existing_job.pk,
        )
        if locked_job.status not in {
            CollectionJob.Status.PENDING,
            CollectionJob.Status.RUNNING,
            CollectionJob.Status.RETRY_WAITING,
        }:
            raise ValueError(
                "Job antrean hanya dapat dimulai dari status Menunggu/Running."
            )
        now = timezone.now()
        was_claimed = locked_job.status == CollectionJob.Status.RUNNING
        locked_job.source = source
        locked_job.session = session or locked_job.session
        locked_job.job_type = job_type
        locked_job.crawler_name = crawler_name
        locked_job.status = CollectionJob.Status.RUNNING
        locked_job.started_at = now
        locked_job.finished_at = None
        locked_job.triggered_by = triggered_by
        locked_job.trigger_type = trigger_type
        locked_job.queue_key = locked_job.queue_key or queue_key_for(
            source_id=source.pk if source else None,
            job_type=job_type,
        )
        if not was_claimed:
            locked_job.attempt_count += 1
            locked_job.last_attempt_at = now
        locked_job.heartbeat_at = now
        locked_job.lease_expires_at = now + timedelta(
            seconds=int(getattr(settings, "COLLECTION_JOB_LEASE_SECONDS", 180))
        )
        locked_job.metadata = {
            **locked_job.metadata,
            **(metadata or {}),
        }
        locked_job.save(
            update_fields=[
                "source",
                "session",
                "job_type",
                "crawler_name",
                "status",
                "started_at",
                "finished_at",
                "triggered_by",
                "trigger_type",
                "queue_key",
                "attempt_count",
                "last_attempt_at",
                "heartbeat_at",
                "lease_expires_at",
                "metadata",
                "updated_at",
            ]
        )
        return locked_job

    now = timezone.now()
    return CollectionJob.objects.create(
        source=source,
        session=session,
        job_type=job_type,
        crawler_name=crawler_name,
        status=CollectionJob.Status.RUNNING,
        started_at=now,
        available_at=now,
        attempt_count=1,
        last_attempt_at=now,
        heartbeat_at=now,
        lease_expires_at=now + timedelta(
            seconds=int(getattr(settings, "COLLECTION_JOB_LEASE_SECONDS", 180))
        ),
        queue_key=queue_key_for(
            source_id=source.pk if source else None,
            job_type=job_type,
        ),
        triggered_by=triggered_by,
        trigger_type=trigger_type,
        metadata=metadata or {},
    )


@transaction.atomic
def complete_collection_job(
    *,
    job: CollectionJob,
    total_found: int,
    total_created: int,
    total_duplicate: int,
    total_rejected: int,
    total_failed: int,
) -> CollectionJob:
    total_success = total_created + total_duplicate

    if (
        total_failed > 0
        and total_failed == total_found
        and total_success == 0
        and total_rejected == 0
    ):
        # SEMUA yang ditemukan gagal, tidak ada satupun kandidat yang
        # sempat diproses jadi kategori lain (created/duplicate/
        # rejected) -- ini kegagalan total (mis. seed listing sendiri
        # gagal di-fetch/diblokir), bukan "selesai dengan kesalahan
        # kecil" seperti kasus sebagian kandidat ditolak karena tidak
        # relevan (itu tetap dianggap berhasil diproses).
        status = CollectionJob.Status.FAILED
    elif total_failed > 0:
        status = CollectionJob.Status.COMPLETED_WITH_ERRORS
    else:
        status = CollectionJob.Status.COMPLETED

    job.status = status
    job.finished_at = timezone.now()
    job.total_found = total_found
    job.total_created = total_created
    job.total_duplicate = total_duplicate
    job.total_rejected = total_rejected
    job.total_failed = total_failed
    job.worker_id = ""
    job.lease_expires_at = None
    job.heartbeat_at = job.finished_at

    job.save(
        update_fields=[
            "status",
            "finished_at",
            "total_found",
            "total_created",
            "total_duplicate",
            "total_rejected",
            "total_failed",
            "worker_id",
            "lease_expires_at",
            "heartbeat_at",
            "updated_at",
        ]
    )

    if job.source_id:
        Source.objects.filter(
            pk=job.source_id,
        ).update(
            last_crawled_at=job.finished_at,
        )

    return job


@transaction.atomic
def fail_collection_job(
    *,
    job: CollectionJob,
    error_message: str,
    total_found: int | None = None,
    total_created: int | None = None,
    total_duplicate: int | None = None,
    total_rejected: int | None = None,
    total_failed: int | None = None,
) -> CollectionJob:
    job.status = CollectionJob.Status.FAILED
    job.finished_at = timezone.now()
    job.error_message = error_message

    if total_found is not None:
        job.total_found = total_found

    if total_created is not None:
        job.total_created = total_created

    if total_duplicate is not None:
        job.total_duplicate = total_duplicate

    if total_rejected is not None:
        job.total_rejected = total_rejected

    failed_count = (
        job.total_failed
        if total_failed is None
        else total_failed
    )
    job.total_failed = max(failed_count, 1)
    job.worker_id = ""
    job.lease_expires_at = None
    job.heartbeat_at = job.finished_at

    job.save(
        update_fields=[
            "status",
            "finished_at",
            "error_message",
            "total_found",
            "total_created",
            "total_duplicate",
            "total_rejected",
            "total_failed",
            "worker_id",
            "lease_expires_at",
            "heartbeat_at",
            "updated_at",
        ]
    )

    if job.source_id:
        Source.objects.filter(
            pk=job.source_id,
        ).update(
            last_crawled_at=job.finished_at,
        )

    return job
