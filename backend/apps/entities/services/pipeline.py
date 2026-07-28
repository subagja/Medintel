from dataclasses import dataclass

from django.db import transaction

from apps.articles.models import Article

from .fact_extraction import (
    FactExtractionResult,
    extract_article_facts,
)
from .rule_based import (
    EntityExtractionResult,
    extract_article_entities,
)


@dataclass(frozen=True)
class ArticleExploitationResult:
    article: Article
    entity_result: EntityExtractionResult
    fact_result: FactExtractionResult


@transaction.atomic
def exploit_article(
    article: Article,
) -> ArticleExploitationResult:
    entity_result = extract_article_entities(
        article
    )

    fact_result = extract_article_facts(
        article
    )

    article.processing_status = (
        Article.ProcessingStatus.PROCESSED
    )

    article.save(
        update_fields=[
            "processing_status",
            "updated_at",
        ]
    )

    return ArticleExploitationResult(
        article=article,
        entity_result=entity_result,
        fact_result=fact_result,
    )