import logging

from django.utils import timezone

from apps.collection.models import (
    CollectionJob,
    CollectionJobItem,
    CollectionSession,
)
from apps.collection.services import (
    complete_collection_job,
    fail_collection_job,
    start_collection_job,
)
from apps.collection.services.queue import (
    log_collection_job,
    mark_collection_job_interrupted,
    touch_collection_job,
)
from apps.collection.services.queue import enqueue_collection_job
from apps.ingestion.services import ingest_article
from apps.sources.models import Source

from .base import BaseCrawler
from .results import (
    CrawlExecutionResult,
    CrawlItemEvent,
    CrawlItemStatus,
)


logger = logging.getLogger(__name__)


class CollectionJobPauseRequested(Exception):
    pass


class CollectionJobCancelRequested(Exception):
    pass


class CollectionJobLeaseLost(Exception):
    pass


def run_crawler(
    crawler: BaseCrawler,
    *,
    triggered_by=None,
    trigger_type: str = "system",
    session: CollectionSession | None = None,
    existing_job_id=None,
) -> CrawlExecutionResult:
    total_found = 0
    total_created = 0
    total_duplicate = 0
    total_rejected = 0
    total_failed = 0

    is_global_discovery = bool(
        getattr(crawler, "global_discovery", False)
    )
    source = None
    if not is_global_discovery:
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

    source_code = source.code if source else "lintas-sumber"

    requested_job_type = getattr(
        crawler,
        "job_type",
        CollectionJob.JobType.CRAWLER,
    )
    valid_job_types = {
        value
        for value, _label in CollectionJob.JobType.choices
    }
    job_type = (
        requested_job_type
        if requested_job_type in valid_job_types
        else CollectionJob.JobType.CRAWLER
    )

    existing_job = None
    if existing_job_id is not None:
        existing_job = CollectionJob.objects.get(pk=existing_job_id)

    job = start_collection_job(
        source=source,
        job_type=job_type,
        crawler_name=crawler.__class__.__name__,
        triggered_by=triggered_by,
        trigger_type=trigger_type,
        session=session,
        existing_job=existing_job,
        metadata={
            "source_code": source_code,
            "discovery_scope": (
                "global_allowed_sources"
                if is_global_discovery
                else "single_source"
            ),
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
            "max_age_days": getattr(
                crawler,
                "max_age_days",
                None,
            ),
            "collection_channel": getattr(
                crawler,
                "discovery_channel",
                "publisher_html",
            ),
        },
    )

    # Retry dan pemulihan tetap memakai job yang sama. Total serta item
    # terminal dipertahankan agar kandidat yang sudah selesai tidak dihitung
    # atau diproses ulang.
    total_found = job.total_found
    total_created = job.total_created
    total_duplicate = job.total_duplicate
    total_rejected = job.total_rejected
    total_failed = job.total_failed
    previously_processed_urls = set(
        job.items.exclude(status=CollectionJobItem.Status.FOUND)
        .values_list("original_url", flat=True)
    )

    def current_totals() -> dict[str, int]:
        return {
            "total_found": total_found,
            "total_created": total_created,
            "total_duplicate": total_duplicate,
            "total_rejected": total_rejected,
            "total_failed": total_failed,
        }

    def checkpoint() -> None:
        control = CollectionJob.objects.filter(pk=job.pk).values(
            "status",
            "worker_id",
            "pause_requested_at",
            "cancel_requested_at",
        ).first()
        if not control:
            raise CollectionJobLeaseLost("Job tidak lagi tersedia.")
        if (
            control["status"] != CollectionJob.Status.RUNNING
            or (job.worker_id and control["worker_id"] != job.worker_id)
        ):
            raise CollectionJobLeaseLost(
                "Lease worker tidak lagi berlaku; eksekusi lama dihentikan."
            )
        if control["cancel_requested_at"]:
            raise CollectionJobCancelRequested(
                "Pembatalan diminta oleh pengguna."
            )
        if control["pause_requested_at"]:
            raise CollectionJobPauseRequested("Jeda diminta oleh pengguna.")
        touch_collection_job(
            job_id=job.id,
            worker_id=job.worker_id,
            totals=current_totals(),
            lease_seconds=job.metadata.get("lease_seconds"),
        )

    checkpoint()

    def persist_execution_metadata() -> None:
        metadata_builder = getattr(
            crawler,
            "get_execution_metadata",
            None,
        )
        if not callable(metadata_builder):
            return
        try:
            execution_metadata = metadata_builder()
        except Exception:
            logger.exception(
                "Metadata eksekusi crawler gagal disimpan job=%s",
                job.id,
            )
            return
        if not isinstance(execution_metadata, dict):
            return
        job.metadata = {
            **job.metadata,
            **execution_metadata,
        }
        job.save(update_fields=["metadata", "updated_at"])

    # Menyimpan URL yang sudah ditemukan dalam satu eksekusi crawler.
    # URL yang sama tidak akan dibuat sebagai CollectionJobItem.
    seen_urls: set[str] = set(previously_processed_urls)

    def record_item_event(
        event: CrawlItemEvent,
    ) -> None:
        nonlocal total_found
        nonlocal total_duplicate
        nonlocal total_rejected
        nonlocal total_failed

        checkpoint()

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
                source_code,
                event.status,
            )
            return

        status = event.status
        valid_statuses = {
            CrawlItemStatus.FOUND,
            CrawlItemStatus.DUPLICATE,
            CrawlItemStatus.REJECTED,
            CrawlItemStatus.FAILED,
            CrawlItemStatus.METADATA_ONLY,
            CrawlItemStatus.FETCH_BLOCKED,
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

        # Baris database sudah informatif dari event sebelumnya untuk
        # URL yang sama (mis. entri RSS duplikat dalam satu proses) --
        # baris TIDAK perlu ditimpa lagi, TAPI ringkasan (total_found/
        # total_duplicate/dst) tetap harus dihitung, karena event ini
        # tetap mewakili kejadian nyata yang terjadi saat crawling.
        skip_row_update = (
            not item_created
            and item.status != CollectionJobItem.Status.FOUND
        )

        total_found += 1

        if status == CrawlItemStatus.DUPLICATE:
            total_duplicate += 1
        elif status in {
            CrawlItemStatus.REJECTED,
            CrawlItemStatus.METADATA_ONLY,
        }:
            total_rejected += 1
        else:
            total_failed += 1

        # Heartbeat/lease job tetap diperbarui untuk SETIAP event nyata
        # (termasuk yang barisnya tidak perlu ditimpa), supaya job yang
        # sedang memproses banyak duplikat berturut-turut tidak salah
        # dianggap macet oleh pembersih job basi.
        touch_collection_job(
            job_id=job.id,
            worker_id=job.worker_id,
            totals=current_totals(),
            lease_seconds=job.metadata.get("lease_seconds"),
        )

        # URL yang sama dapat sudah memiliki hasil lebih informatif pada
        # job ini (baik karena baris sudah informatif sebelumnya, maupun
        # karena baris ini baru pertama kali dibuat langsung sebagai
        # DUPLICATE). Jangan menimpanya hanya karena ditemukan lagi.
        if skip_row_update or (
            status == CrawlItemStatus.DUPLICATE and not item_created
        ):
            return

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
            checkpoint()

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

            if original_url in previously_processed_urls:
                continue

            total_found += 1

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

                    # Ekstraksi otomatis: begitu artikel baru tersimpan,
                    # langsung ekstrak penyakit/lokasi/fakta -- supaya
                    # analis tidak perlu jalankan `process_articles`
                    # manual lagi setiap habis crawling. Dibungkus
                    # try/except supaya kegagalan ekstraksi (mis. bug
                    # parsing pada satu artikel) tidak menggagalkan
                    # keseluruhan crawl job.
                    try:
                        from apps.entities.services import exploit_article

                        exploit_article(result.article)
                    except Exception:
                        logger.exception(
                            (
                                "Ekstraksi otomatis gagal untuk artikel "
                                "job=%s article=%s"
                            ),
                            job.id,
                            result.article.id if result.article else None,
                        )

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

            checkpoint()

        persist_execution_metadata()

        complete_collection_job(
            job=job,
            total_found=total_found,
            total_created=total_created,
            total_duplicate=total_duplicate,
            total_rejected=total_rejected,
            total_failed=total_failed,
        )

    except CollectionJobLeaseLost as exc:
        persist_execution_metadata()
        job.refresh_from_db()
        log_collection_job(
            job,
            event="lease_lost",
            message=str(exc),
            level="warning",
        )

    except CollectionJobPauseRequested as exc:
        persist_execution_metadata()
        mark_collection_job_interrupted(
            job=job,
            status=CollectionJob.Status.PAUSED,
            totals=current_totals(),
            message=str(exc),
        )

    except CollectionJobCancelRequested as exc:
        persist_execution_metadata()
        mark_collection_job_interrupted(
            job=job,
            status=CollectionJob.Status.CANCELLED,
            totals=current_totals(),
            message=str(exc),
        )

    except Exception as exc:
        total_failed += 1

        persist_execution_metadata()

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


def run_crawler_in_background(
    crawler: BaseCrawler,
    *,
    triggered_by=None,
    trigger_type: str = "system",
    session: CollectionSession | None = None,
    existing_job_id=None,
) -> CollectionJob:
    """Kompatibilitas lama: masukkan crawler ke antrean, tanpa thread Django."""
    if existing_job_id is not None:
        return CollectionJob.objects.get(pk=existing_job_id)
    is_global = bool(getattr(crawler, "global_discovery", False))
    source = None
    if not is_global:
        source = Source.objects.get(code=crawler.source_code)
    job_type = getattr(crawler, "job_type", CollectionJob.JobType.CRAWLER)
    return enqueue_collection_job(
        source=source,
        session=session,
        job_type=job_type,
        crawler_name=crawler.__class__.__name__,
        triggered_by=triggered_by,
        trigger_type=trigger_type,
        metadata={
            "article_limit": getattr(crawler, "limit", None),
            "candidate_limit": getattr(crawler, "candidate_limit", None),
            "max_age_days": getattr(crawler, "max_age_days", None),
            "collection_channel": getattr(
                crawler, "discovery_channel", "publisher_html"
            ),
        },
    )


enqueue_crawler = run_crawler_in_background
