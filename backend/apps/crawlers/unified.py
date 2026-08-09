from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import close_old_connections, transaction

from apps.collection.models import CollectionJob, CollectionSession
from apps.sources.models import Source, SourceSeedUrl
from apps.sources.services import check_source_seed_readiness

from .google_news_crawler import (
    GoogleNewsRssCrawler,
    get_google_news_allowed_sources,
)
from .google_news_queries import build_google_news_disease_scope
from .real_crawler import GenericHtmlCrawler
from .rss_crawler import OfficialRssCrawler
from .services import run_crawler


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UnifiedSourceReadiness:
    source: Source
    html_ready: bool
    rss_ready: bool

    @property
    def primary_channel(self) -> str:
        return "rss" if self.rss_ready else "html"


@dataclass(frozen=True)
class UnifiedJobSpec:
    source: Source | None
    job_type: str
    crawler_name: str
    channel_label: str

    @property
    def source_code(self) -> str:
        return self.source.code if self.source else "google-news-global"


@dataclass(frozen=True)
class UnifiedCollectionPlan:
    scope: str
    selected_source: Source | None
    selected_rows: tuple[UnifiedSourceReadiness, ...]
    specs: tuple[UnifiedJobSpec, ...]
    skipped: tuple[dict, ...]
    google_news_source_codes: tuple[str, ...]


def get_unified_source_readiness() -> list[UnifiedSourceReadiness]:
    rows: list[UnifiedSourceReadiness] = []
    sources = (
        Source.objects.prefetch_related("seed_urls", "url_patterns")
        .order_by("name")
    )
    for source in sources:
        html_ready = check_source_seed_readiness(
            source,
            seed_types=(
                SourceSeedUrl.SeedType.LISTING,
                SourceSeedUrl.SeedType.DIRECT,
            ),
        ).is_ready
        rss_ready = check_source_seed_readiness(
            source,
            seed_types=(SourceSeedUrl.SeedType.RSS,),
        ).is_ready
        if html_ready or rss_ready:
            rows.append(
                UnifiedSourceReadiness(
                    source=source,
                    html_ready=html_ready,
                    rss_ready=rss_ready,
                )
            )
    return rows


def build_unified_collection_plan(
    *,
    source_code: str,
    include_google_news: bool,
    html_deep_scan: bool,
) -> UnifiedCollectionPlan:
    readiness_rows = get_unified_source_readiness()
    by_code = {row.source.code: row for row in readiness_rows}

    selected_source = None
    if source_code == CollectionSession.Scope.ALL_READY:
        scope = CollectionSession.Scope.ALL_READY
        selected_rows = readiness_rows
    elif source_code == CollectionSession.Scope.ALL_INDONESIA:
        scope = CollectionSession.Scope.ALL_INDONESIA
        selected_rows = [
            row
            for row in readiness_rows
            if row.source.source_type
            != Source.SourceType.INTERNATIONAL_MEDIA
        ]
    else:
        scope = CollectionSession.Scope.SINGLE_SOURCE
        row = by_code.get(source_code)
        if row is None:
            raise ValidationError(
                "Source tidak ditemukan atau belum siap pada kanal HTML/RSS."
            )
        selected_source = row.source
        selected_rows = [row]

    if not selected_rows:
        raise ValidationError(
            "Belum ada Source yang siap untuk Koleksi Terpadu."
        )

    requested_specs: list[UnifiedJobSpec] = []
    for row in selected_rows:
        if row.rss_ready:
            requested_specs.append(
                UnifiedJobSpec(
                    source=row.source,
                    job_type=CollectionJob.JobType.RSS,
                    crawler_name=OfficialRssCrawler.__name__,
                    channel_label="RSS resmi",
                )
            )
            if html_deep_scan and row.html_ready:
                requested_specs.append(
                    UnifiedJobSpec(
                        source=row.source,
                        job_type=CollectionJob.JobType.CRAWLER,
                        crawler_name=GenericHtmlCrawler.__name__,
                        channel_label="HTML deep scan",
                    )
                )
        elif row.html_ready:
            requested_specs.append(
                UnifiedJobSpec(
                    source=row.source,
                    job_type=CollectionJob.JobType.CRAWLER,
                    crawler_name=GenericHtmlCrawler.__name__,
                    channel_label="HTML fallback",
                )
            )

    selected_codes = {row.source.code for row in selected_rows}
    google_news_codes = tuple(
        source.code
        for source in get_google_news_allowed_sources()
        if source.code in selected_codes
    )
    skipped: list[dict] = []
    if include_google_news:
        if not build_google_news_disease_scope().batches:
            skipped.append(
                {
                    "source_code": "google-news-global",
                    "job_type": CollectionJob.JobType.GOOGLE_NEWS,
                    "reason": "Disease Master belum memiliki cakupan aktif.",
                }
            )
        elif not google_news_codes:
            skipped.append(
                {
                    "source_code": "google-news-global",
                    "job_type": CollectionJob.JobType.GOOGLE_NEWS,
                    "reason": "Tidak ada Source dalam cakupan yang lolos whitelist.",
                }
            )
        else:
            requested_specs.append(
                UnifiedJobSpec(
                    source=None,
                    job_type=CollectionJob.JobType.GOOGLE_NEWS,
                    crawler_name=GoogleNewsRssCrawler.__name__,
                    channel_label="Google News discovery",
                )
            )

    specs: list[UnifiedJobSpec] = []
    for spec in requested_specs:
        running = CollectionJob.objects.filter(
            source=spec.source,
            job_type=spec.job_type,
            status__in=(
                CollectionJob.Status.PENDING,
                CollectionJob.Status.RUNNING,
            ),
        ).exists()
        if running:
            skipped.append(
                {
                    "source_code": spec.source_code,
                    "job_type": spec.job_type,
                    "reason": "Kanal yang sama masih berjalan.",
                }
            )
        else:
            specs.append(spec)

    if not specs:
        raise ValidationError(
            "Tidak ada job baru yang dapat dimulai; kanal terpilih masih "
            "berjalan atau belum siap."
        )

    return UnifiedCollectionPlan(
        scope=scope,
        selected_source=selected_source,
        selected_rows=tuple(selected_rows),
        specs=tuple(specs),
        skipped=tuple(skipped),
        google_news_source_codes=google_news_codes,
    )


def _crawler_for_job(job: CollectionJob, session: CollectionSession):
    common = {
        "limit": session.article_limit,
        "candidate_limit": session.candidate_limit,
    }
    if job.job_type == CollectionJob.JobType.RSS:
        return OfficialRssCrawler(source_code=job.source.code, **common)
    if job.job_type == CollectionJob.JobType.GOOGLE_NEWS:
        return GoogleNewsRssCrawler(
            allowed_source_codes=tuple(
                session.metadata.get("google_news_source_codes", ())
            ),
            **common,
        )
    return GenericHtmlCrawler(source_code=job.source.code, **common)


def run_unified_session_in_background(
    session_id,
    *,
    max_workers: int = 3,
) -> threading.Thread:
    """Jalankan job terencana dengan worker terbatas agar server tetap stabil."""

    worker_count = min(max(int(max_workers), 1), 5)

    def _orchestrate() -> None:
        close_old_connections()
        try:
            session = CollectionSession.objects.get(pk=session_id)
            job_ids = list(
                session.jobs.filter(status=CollectionJob.Status.PENDING)
                .order_by("created_at")
                .values_list("id", flat=True)
            )
            work_queue: queue.Queue = queue.Queue()
            for job_id in job_ids:
                work_queue.put(job_id)

            def _worker() -> None:
                close_old_connections()
                try:
                    while True:
                        try:
                            job_id = work_queue.get_nowait()
                        except queue.Empty:
                            return
                        try:
                            job = CollectionJob.objects.select_related(
                                "source", "session", "triggered_by"
                            ).get(pk=job_id)
                            if job.status != CollectionJob.Status.PENDING:
                                continue
                            crawler = _crawler_for_job(job, session)
                            run_crawler(
                                crawler,
                                triggered_by=session.triggered_by,
                                trigger_type=session.trigger_type,
                                session=session,
                                existing_job_id=job.id,
                            )
                        except Exception:
                            logger.exception(
                                "Job Koleksi Terpadu gagal session=%s job=%s",
                                session_id,
                                job_id,
                            )
                        finally:
                            work_queue.task_done()
                finally:
                    close_old_connections()

            workers = [
                threading.Thread(
                    target=_worker,
                    name=f"unified-collection-{session_id}-{index + 1}",
                    daemon=True,
                )
                for index in range(min(worker_count, len(job_ids)))
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
        finally:
            close_old_connections()

    orchestrator = threading.Thread(
        target=_orchestrate,
        name=f"unified-collection-session-{session_id}",
        daemon=True,
    )
    orchestrator.start()
    return orchestrator


@transaction.atomic
def start_unified_collection(
    *,
    source_code: str,
    include_google_news: bool,
    html_deep_scan: bool,
    article_limit: int | None,
    candidate_limit: int | None,
    triggered_by=None,
    trigger_type: str = "user",
) -> CollectionSession:
    plan = build_unified_collection_plan(
        source_code=source_code,
        include_google_news=include_google_news,
        html_deep_scan=html_deep_scan,
    )
    session = CollectionSession.objects.create(
        scope=plan.scope,
        selected_source=plan.selected_source,
        include_google_news=include_google_news,
        html_deep_scan=html_deep_scan,
        article_limit=article_limit,
        candidate_limit=candidate_limit,
        planned_job_count=len(plan.specs),
        skipped_job_count=len(plan.skipped),
        triggered_by=triggered_by,
        trigger_type=trigger_type,
        metadata={
            "policy": "rss_primary_html_fallback",
            "selected_source_codes": [
                row.source.code for row in plan.selected_rows
            ],
            "google_news_source_codes": list(
                plan.google_news_source_codes
            ),
            "skipped": list(plan.skipped),
        },
    )
    for spec in plan.specs:
        CollectionJob.objects.create(
            session=session,
            source=spec.source,
            job_type=spec.job_type,
            crawler_name=spec.crawler_name,
            status=CollectionJob.Status.PENDING,
            triggered_by=triggered_by,
            trigger_type=trigger_type,
            metadata={
                "unified_session": session.reference,
                "channel_role": spec.channel_label,
                "collection_channel": (
                    "official_rss"
                    if spec.job_type == CollectionJob.JobType.RSS
                    else (
                        "google_news_rss"
                        if spec.job_type == CollectionJob.JobType.GOOGLE_NEWS
                        else "publisher_html"
                    )
                ),
            },
        )

    transaction.on_commit(
        lambda: run_unified_session_in_background(session.pk)
    )
    return session
