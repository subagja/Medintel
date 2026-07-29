import logging

from django.utils import timezone

from apps.collection.models import (
    CollectionJob,
    CollectionJobItem,
)
from apps.collection.services import (
    complete_collection_job,
    fail_collection_job,
    start_collection_job,
)
from apps.ingestion.services import ingest_article
from apps.sources.models import Source

from .base import BaseCrawler
from .results import CrawlExecutionResult


logger = logging.getLogger(__name__)


def run_crawler(
    crawler: BaseCrawler,
    *,
    triggered_by=None,
    trigger_type: str = "system",
) -> CrawlExecutionResult:
    total_found = 0
    total_created = 0
    total_duplicate = 0
    total_rejected = 0
    total_failed = 0

    try:
        source = Source.objects.get(
            code=crawler.source_code,
        )
    except Source.DoesNotExist:
        logger.error(
            "Sumber crawler tidak terdaftar: %s",
            crawler.source_code,
        )

        return CrawlExecutionResult(
            total_found=0,
            total_created=0,
            total_duplicate=0,
            total_rejected=0,
            total_failed=1,
        )

    job = start_collection_job(
        source=source,
        job_type=CollectionJob.JobType.CRAWLER,
        crawler_name=crawler.__class__.__name__,
        triggered_by=triggered_by,
        trigger_type=trigger_type,
        metadata={
            "source_code": crawler.source_code,
        },
    )

    try:
        payloads = crawler.crawl()

        for payload in payloads:
            total_found += 1

            item = CollectionJobItem.objects.create(
                collection_job=job,
                original_url=payload.url,
                title=payload.title,
                status=CollectionJobItem.Status.FOUND,
                metadata={
                    "source_code": payload.source_code,
                    "payload_metadata": payload.metadata,
                },
            )

            try:
                result = ingest_article(payload)

                item.article = result.article
                item.reason = result.reason
                item.processed_at = timezone.now()

                if result.status == "created":
                    total_created += 1
                    item.status = CollectionJobItem.Status.CREATED

                elif result.status == "duplicate":
                    total_duplicate += 1
                    item.status = CollectionJobItem.Status.DUPLICATE

                elif result.status == "rejected":
                    total_rejected += 1
                    item.status = CollectionJobItem.Status.REJECTED

                else:
                    total_failed += 1
                    item.status = CollectionJobItem.Status.FAILED

                if result.article:
                    item.normalized_url = result.article.normalized_url

                item.save(
                    update_fields=[
                        "article",
                        "normalized_url",
                        "status",
                        "reason",
                        "processed_at",
                        "updated_at",
                    ]
                )

                logger.info(
                    "Collection item source=%s status=%s reason=%s",
                    payload.source_code,
                    result.status,
                    result.reason,
                )

            except Exception as exc:
                total_failed += 1

                item.status = CollectionJobItem.Status.FAILED
                item.error_message = str(exc)
                item.processed_at = timezone.now()

                item.save(
                    update_fields=[
                        "status",
                        "error_message",
                        "processed_at",
                        "updated_at",
                    ]
                )

                logger.exception(
                    "Gagal memproses artikel source=%s url=%s",
                    payload.source_code,
                    payload.url,
                )

        complete_collection_job(
            job=job,
            total_found=total_found,
            total_created=total_created,
            total_duplicate=total_duplicate,
            total_rejected=total_rejected,
            total_failed=total_failed,
        )

    except Exception as exc:
        total_failed += 1

        fail_collection_job(
            job=job,
            error_message=str(exc),
        )

        logger.exception(
            "Crawler gagal dijalankan: %s",
            crawler.__class__.__name__,
        )
        
        raise

    return CrawlExecutionResult(
        total_found=total_found,
        total_created=total_created,
        total_duplicate=total_duplicate,
        total_rejected=total_rejected,
        total_failed=total_failed,
    )