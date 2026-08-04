from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Callable

from apps.ingestion.dto import ArticlePayload

from .results import CrawlItemEvent


class BaseCrawler(ABC):
    source_code: str

    def set_item_observer(
        self,
        observer: Callable[[CrawlItemEvent], None] | None,
    ) -> None:
        self._item_observer = observer

    def emit_item_event(
        self,
        event: CrawlItemEvent,
    ) -> None:
        observer = getattr(
            self,
            "_item_observer",
            None,
        )

        if observer is not None:
            observer(event)

    @abstractmethod
    def crawl(self) -> Iterable[ArticlePayload]:
        """
        Menghasilkan artikel dalam format ArticlePayload.
        Crawler tidak boleh menyimpan langsung ke database.
        """
        raise NotImplementedError
