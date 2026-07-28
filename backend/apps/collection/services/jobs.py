from django.db import transaction
from django.utils import timezone

from apps.sources.models import Source

from ..models import CollectionJob


@transaction.atomic
def start_collection_job(
    *,
    source: Source,
    job_type: str,
    crawler_name: str = "",
    triggered_by=None,
    trigger_type: str = "system",
    metadata: dict | None = None,
) -> CollectionJob:
    return CollectionJob.objects.create(
        source=source,
        job_type=job_type,
        crawler_name=crawler_name,
        status=CollectionJob.Status.RUNNING,
        started_at=timezone.now(),
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
    if total_failed > 0:
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

    job.save(
        update_fields=[
            "status",
            "finished_at",
            "total_found",
            "total_created",
            "total_duplicate",
            "total_rejected",
            "total_failed",
            "updated_at",
        ]
    )

    return job


@transaction.atomic
def fail_collection_job(
    *,
    job: CollectionJob,
    error_message: str,
) -> CollectionJob:
    job.status = CollectionJob.Status.FAILED
    job.finished_at = timezone.now()
    job.error_message = error_message
    job.total_failed = max(job.total_failed, 1)

    job.save(
        update_fields=[
            "status",
            "finished_at",
            "error_message",
            "total_failed",
            "updated_at",
        ]
    )

    return job