from dataclasses import dataclass, field


class CrawlItemStatus:
    FOUND = "found"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    FAILED = "failed"
    METADATA_ONLY = "metadata_only"
    FETCH_BLOCKED = "fetch_blocked"


@dataclass(frozen=True)
class CrawlItemEvent:
    original_url: str
    status: str
    normalized_url: str = ""
    title: str = ""
    reason: str = ""
    error_message: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CrawlExecutionResult:
    total_found: int
    total_created: int
    total_duplicate: int
    total_rejected: int
    total_failed: int
    job_id: str | None = None
