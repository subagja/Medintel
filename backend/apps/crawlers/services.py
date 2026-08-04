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
from .results import (
    CrawlExecutionResult,
    CrawlItemEvent,
    CrawlItemStatus,
)


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
            "article_limit": getattr(
                crawler,
                "limit",
                None,
            ),
            "candidate_limit": getattr(
                crawler,
                "candidate_limit",
                None,
            ),
        },
    )

    # Menyimpan URL yang sudah ditemukan dalam satu eksekusi crawler.
    # URL yang sama tidak akan dibuat sebagai CollectionJobItem.
    seen_urls: set[str] = set()

    def record_item_event(
        event: CrawlItemEvent,
    ) -> None:
        nonlocal total_found
        nonlocal total_duplicate
        nonlocal total_rejected
        nonlocal total_failed

        original_url = (
            event.original_url or ""
        ).strip()

        if not original_url:
            total_found += 1
            total_failed += 1

            logger.warning(
                "Event crawler tidak memiliki URL "
                "job=%s source=%s status=%s",
                job.id,
                source.code,
                event.status,
            )
            return

        status = event.status
        valid_statuses = {
            CrawlItemStatus.FOUND,
            CrawlItemStatus.DUPLICATE,
            CrawlItemStatus.REJECTED,
            CrawlItemStatus.FAILED,
        }

        if status not in valid_statuses:
            status = CrawlItemStatus.FAILED

        item, item_created = (
            CollectionJobItem.objects.get_or_create(
                collection_job=job,
                original_url=original_url[:1000],
                defaults={
                    "normalized_url": (
                        event.normalized_url or ""
                    )[:1000],
                    "title": (
                        event.title or ""
                    )[:500],
                    "status": status,
                    "reason": event.reason,
                    "error_message": (
                        event.error_message
                    ),
                    "processed_at": (
                        None
                        if status
                        == CrawlItemStatus.FOUND
                        else timezone.now()
                    ),
                    "metadata": event.metadata,
                },
            )
        )

        # Event FOUND hanya menyiapkan item agar hasil ingestion
        # dapat memperbarui baris yang sama.
        if status == CrawlItemStatus.FOUND:
            return

        total_found += 1

        if status == CrawlItemStatus.DUPLICATE:
            total_duplicate += 1

            # URL yang sama dapat sudah memiliki hasil lebih informatif
            # pada job ini. Jangan menimpanya hanya karena ditemukan lagi.
            if not item_created:
                return

        elif status == CrawlItemStatus.REJECTED:
            total_rejected += 1

        else:
            total_failed += 1

        if not item_created:
            item.normalized_url = (
                event.normalized_url
                or item.normalized_url
            )[:1000]
            item.title = (
                event.title
                or item.title
            )[:500]
            item.status = status
            item.reason = event.reason
            item.error_message = (
                event.error_message
            )
            item.processed_at = timezone.now()
            item.metadata = {
                **item.metadata,
                **event.metadata,
            }
            item.save(
                update_fields=[
                    "normalized_url",
                    "title",
                    "status",
                    "reason",
                    "error_message",
                    "processed_at",
                    "metadata",
                    "updated_at",
                ]
            )

    crawler.set_item_observer(
        record_item_event
    )

    try:
        payloads = crawler.crawl()

        for payload in payloads:
            total_found += 1

            original_url = (payload.url or "").strip()

            if not original_url:
                total_failed += 1

                logger.warning(
                    "Payload crawler tidak memiliki URL "
                    "source=%s title=%s",
                    payload.source_code,
                    payload.title,
                )
                continue

            # Duplikat dalam hasil crawler pada job yang sama.
            # Tidak disimpan ke database.
            if original_url in seen_urls:
                total_duplicate += 1

                logger.info(
                    "URL duplikat dalam job tidak disimpan "
                    "job=%s source=%s url=%s",
                    job.id,
                    payload.source_code,
                    original_url,
                )
                continue

            seen_urls.add(original_url)

            # Perlindungan database apabila URL yang sama sudah pernah
            # tercatat pada CollectionJob yang sedang berjalan.
            item, item_created = (
                CollectionJobItem.objects.get_or_create(
                    collection_job=job,
                    original_url=original_url,
                    defaults={
                        "title": payload.title,
                        "status": CollectionJobItem.Status.FOUND,
                        "metadata": {
                            "source_code": payload.source_code,
                            "payload_metadata": payload.metadata,
                        },
                    },
                )
            )

            if not item_created:
                # GenericHtmlCrawler membuat item FOUND melalui
                # observer sebelum payload diberikan untuk ingestion.
                # Item tersebut harus diproses, bukan dianggap duplikat.
                if item.status != CollectionJobItem.Status.FOUND:
                    item.status = CollectionJobItem.Status.FOUND
                    item.reason = ""
                    item.error_message = ""
                    item.save(
                        update_fields=[
                            "status",
                            "reason",
                            "error_message",
                            "updated_at",
                        ]
                    )

            try:
                result = ingest_article(payload)

                # Artikel yang sudah ada tetap dicatat pada riwayat job
                # sebagai CollectionJobItem berstatus DUPLICATE.
                if result.status == "duplicate":
                    total_duplicate += 1

                    logger.info(
                        "Artikel duplikat dicatat pada job "
                        "job=%s source=%s url=%s reason=%s",
                        job.id,
                        payload.source_code,
                        original_url,
                        result.reason,
                    )

                    item.article = result.article
                    item.status = (
                        CollectionJobItem.Status.DUPLICATE
                    )
                    item.reason = result.reason
                    item.processed_at = timezone.now()

                    if result.article:
                        item.normalized_url = (
                            result.article.normalized_url
                        )

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
                    continue

                item.article = result.article
                item.reason = result.reason
                item.processed_at = timezone.now()

                if result.status == "created":
                    total_created += 1
                    item.status = CollectionJobItem.Status.CREATED

                elif result.status == "rejected":
                    total_rejected += 1
                    item.status = CollectionJobItem.Status.REJECTED

                else:
                    total_failed += 1
                    item.status = CollectionJobItem.Status.FAILED

                if result.article:
                    item.normalized_url = (
                        result.article.normalized_url
                    )

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
                    "Collection item source=%s "
                    "status=%s reason=%s",
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
                    "Gagal memproses artikel "
                    "source=%s url=%s",
                    payload.source_code,
                    original_url,
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
            total_found=total_found,
            total_created=total_created,
            total_duplicate=total_duplicate,
            total_rejected=total_rejected,
            total_failed=total_failed,
        )

        logger.exception(
            "Crawler gagal dijalankan: %s",
            crawler.__class__.__name__,
        )

        raise

    finally:
        crawler.set_item_observer(
            None
        )

    return CrawlExecutionResult(
        total_found=total_found,
        total_created=total_created,
        total_duplicate=total_duplicate,
        total_rejected=total_rejected,
        total_failed=total_failed,
        job_id=str(job.id),
    )
