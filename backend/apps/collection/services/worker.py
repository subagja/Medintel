from __future__ import annotations

import logging
from datetime import timedelta

from django.db import OperationalError, close_old_connections
from django.utils import timezone

from apps.crawlers.google_news_crawler import GoogleNewsRssCrawler
from apps.crawlers.http_client import CrawlerHttpError, RobotsDeniedError
from apps.crawlers.real_crawler import GenericHtmlCrawler
from apps.crawlers.rss_crawler import OfficialRssCrawler
from apps.crawlers.services import run_crawler

from ..models import CollectionJob, CollectionJobLog
from .queue import (
    claim_next_collection_job,
    log_collection_job,
    schedule_collection_job_retry,
)


logger = logging.getLogger(__name__)


def build_crawler_for_collection_job(job: CollectionJob):
    session = job.session
    article_limit = (
        session.article_limit
        if session
        else job.metadata.get("article_limit")
    )
    candidate_limit = (
        session.candidate_limit
        if session
        else job.metadata.get("candidate_limit")
    )
    common = {
        "limit": article_limit,
        "candidate_limit": candidate_limit,
    }
    if job.job_type == CollectionJob.JobType.RSS:
        if not job.source_id:
            raise ValueError("Job RSS resmi harus memiliki sumber.")
        return OfficialRssCrawler(source_code=job.source.code, **common)
    if job.job_type == CollectionJob.JobType.GOOGLE_NEWS:
        allowed_source_codes = None
        if session:
            allowed_source_codes = tuple(
                session.metadata.get("google_news_source_codes", ())
            )
        elif job.metadata.get("allowed_source_codes"):
            allowed_source_codes = tuple(job.metadata["allowed_source_codes"])
        return GoogleNewsRssCrawler(
            allowed_source_codes=allowed_source_codes,
            max_age_days=job.metadata.get("max_age_days"),
            **common,
        )
    if not job.source_id:
        raise ValueError("Job HTML harus memiliki sumber.")
    return GenericHtmlCrawler(source_code=job.source.code, **common)


def is_transient_collection_error(exc: Exception) -> bool:
    if isinstance(exc, RobotsDeniedError):
        return False
    if isinstance(
        exc,
        (TimeoutError, ConnectionError, OperationalError, CrawlerHttpError),
    ):
        return True
    message = str(exc).casefold()
    markers = (
        "timeout",
        "timed out",
        "connection",
        "temporarily",
        "sementara",
        "429",
        "500",
        "502",
        "503",
        "504",
        "521",
        "522",
        "database is locked",
    )
    return any(marker in message for marker in markers)


def execute_collection_job(job: CollectionJob) -> CollectionJob:
    close_old_connections()
    try:
        job = CollectionJob.objects.select_related(
            "source", "session", "triggered_by"
        ).get(pk=job.pk)
        crawler = build_crawler_for_collection_job(job)
        log_collection_job(
            job,
            event="started",
            message=f"Eksekusi dimulai melalui {crawler.__class__.__name__}.",
        )
        try:
            result = run_crawler(
                crawler,
                triggered_by=job.triggered_by,
                trigger_type=job.trigger_type,
                session=job.session,
                existing_job_id=job.id,
            )
        except Exception as exc:
            job.refresh_from_db()
            if (
                job.status == CollectionJob.Status.FAILED
                and is_transient_collection_error(exc)
                and schedule_collection_job_retry(
                    job=job,
                    error_message=str(exc),
                )
            ):
                logger.warning("Job %s dijadwalkan retry: %s", job.id, exc)
            else:
                log_collection_job(
                    job,
                    event="failed",
                    message=str(exc) or "Eksekusi crawler gagal.",
                    level=CollectionJobLog.Level.ERROR,
                )
            return CollectionJob.objects.get(pk=job.pk)

        job.refresh_from_db()
        if job.status in {
            CollectionJob.Status.COMPLETED,
            CollectionJob.Status.COMPLETED_WITH_ERRORS,
        }:
            log_collection_job(
                job,
                event="completed",
                message=(
                    "Eksekusi selesai: "
                    f"{result.total_created} baru, "
                    f"{result.total_duplicate} duplikat, "
                    f"{result.total_rejected} ditolak, "
                    f"{result.total_failed} gagal."
                ),
            )
        return job
    finally:
        close_old_connections()


def claim_and_execute_one(*, worker_id: str, lease_seconds: int = 180):
    job = claim_next_collection_job(
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    if job is None:
        return None
    return execute_collection_job(job)


def active_worker_cutoff(*, seconds: int = 30):
    return timezone.now() - timedelta(seconds=max(seconds, 5))
