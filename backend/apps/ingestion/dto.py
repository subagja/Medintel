from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ArticlePayload:
    source_code: str
    url: str
    title: str
    content: str
    published_at: datetime | None = None
    author: str = ""
    excerpt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)