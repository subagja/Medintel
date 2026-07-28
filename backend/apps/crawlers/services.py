import logging

from apps.ingestion.services import ingest_article

from .base import BaseCrawler
from .results import CrawlExecutionResult


logger = logging.getLogger(__name__)


def run_crawler(
    crawler: BaseCrawler,
) -> CrawlExecutionResult:
    total_found = 0
    total_created = 0
    total_duplicate = 0
    total_rejected = 0
    total_failed = 0

    try:
        payloads = crawler.crawl()

        for payload in payloads:
            total_found += 1

            try:
                result = ingest_article(payload)

                if result.status == "created":
                    total_created += 1

                elif result.status == "duplicate":
                    total_duplicate += 1

                elif result.status == "rejected":
                    total_rejected += 1

                else:
                    total_failed += 1

                logger.info(
                    "Crawler result source=%s status=%s reason=%s",
                    payload.source_code,
                    result.status,
                    result.reason,
                )

            except Exception:
                total_failed += 1

                logger.exception(
                    "Gagal memproses artikel source=%s url=%s",
                    payload.source_code,
                    payload.url,
                )

    except Exception:
        logger.exception(
            "Crawler gagal dijalankan: %s",
            crawler.__class__.__name__,
        )

        total_failed += 1

    return CrawlExecutionResult(
        total_found=total_found,
        total_created=total_created,
        total_duplicate=total_duplicate,
        total_rejected=total_rejected,
        total_failed=total_failed,
    )