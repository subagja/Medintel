from abc import ABC, abstractmethod
from collections.abc import Iterable

from apps.ingestion.dto import ArticlePayload


class BaseCrawler(ABC):
    source_code: str

    @abstractmethod
    def crawl(self) -> Iterable[ArticlePayload]:
        """
        Menghasilkan artikel dalam format ArticlePayload.
        Crawler tidak boleh menyimpan langsung ke database.
        """
        raise NotImplementedError