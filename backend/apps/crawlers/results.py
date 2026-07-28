from dataclasses import dataclass


@dataclass(frozen=True)
class CrawlExecutionResult:
    total_found: int
    total_created: int
    total_duplicate: int
    total_rejected: int
    total_failed: int