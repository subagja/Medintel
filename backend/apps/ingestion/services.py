import hashlib
import re
from dataclasses import dataclass

from django.db import IntegrityError, transaction

from apps.articles.models import Article
from apps.sources.models import Source
from apps.sources.services import validate_source_url

from .dto import ArticlePayload


MIN_TITLE_LENGTH = 10
MIN_CONTENT_LENGTH = 100


@dataclass(frozen=True)
class IngestionResult:
    article: Article | None
    created: bool
    status: str
    reason: str = ""


def clean_text(value: str) -> str:
    if not value:
        return ""

    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def create_content_hash(
    title: str,
    content: str,
) -> str:
    normalized_text = f"{clean_text(title)}\n{clean_text(content)}".lower()

    return hashlib.sha256(
        normalized_text.encode("utf-8")
    ).hexdigest()


def validate_payload(
    payload: ArticlePayload,
) -> tuple[bool, str]:
    title = clean_text(payload.title)
    content = clean_text(payload.content)

    if not payload.source_code.strip():
        return False, "Kode sumber tidak boleh kosong."

    if not payload.url.strip():
        return False, "URL tidak boleh kosong."

    if len(title) < MIN_TITLE_LENGTH:
        return False, "Judul artikel terlalu pendek."

    if len(content) < MIN_CONTENT_LENGTH:
        return False, "Isi artikel terlalu pendek."

    return True, ""


@transaction.atomic
def ingest_article(
    payload: ArticlePayload,
) -> IngestionResult:
    is_payload_valid, payload_reason = validate_payload(payload)

    if not is_payload_valid:
        return IngestionResult(
            article=None,
            created=False,
            status="rejected",
            reason=payload_reason,
        )

    try:
        source = Source.objects.get(
            code=payload.source_code,
        )
    except Source.DoesNotExist:
        return IngestionResult(
            article=None,
            created=False,
            status="rejected",
            reason="Sumber tidak terdaftar.",
        )

    url_validation = validate_source_url(
        source,
        payload.url,
    )

    if not url_validation.is_valid:
        return IngestionResult(
            article=None,
            created=False,
            status="rejected",
            reason=url_validation.reason,
        )

    title = clean_text(payload.title)
    content = clean_text(payload.content)
    excerpt = clean_text(payload.excerpt)
    author = clean_text(payload.author)

    content_hash = create_content_hash(
        title=title,
        content=content,
    )

    existing_by_url = Article.objects.filter(
        normalized_url=url_validation.normalized_url,
    ).first()

    if existing_by_url:
        return IngestionResult(
            article=existing_by_url,
            created=False,
            status="duplicate",
            reason="Artikel dengan URL yang sama sudah tersimpan.",
        )

    existing_by_content = Article.objects.filter(
        source=source,
        content_hash=content_hash,
    ).first()

    if existing_by_content:
        return IngestionResult(
            article=existing_by_content,
            created=False,
            status="duplicate",
            reason="Artikel dengan isi yang sama sudah tersimpan.",
        )

    try:
        article = Article.objects.create(
            source=source,
            original_url=payload.url,
            normalized_url=url_validation.normalized_url,
            title=title,
            content_text=content,
            excerpt=excerpt,
            author=author,
            published_at=payload.published_at,
            content_hash=content_hash,
            processing_status=Article.ProcessingStatus.VALIDATED,
            raw_metadata=payload.metadata,
        )
    except IntegrityError:
        existing_article = Article.objects.filter(
            normalized_url=url_validation.normalized_url,
        ).first()

        return IngestionResult(
            article=existing_article,
            created=False,
            status="duplicate",
            reason="Artikel sudah tersimpan oleh proses lain.",
        )

    return IngestionResult(
        article=article,
        created=True,
        status="created",
        reason="Artikel berhasil disimpan.",
    )