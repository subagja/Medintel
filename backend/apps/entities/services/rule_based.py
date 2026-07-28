import re
from dataclasses import dataclass, field

from django.db import transaction

from apps.articles.models import Article

from ..models import (
    ArticleDisease,
    ArticleLocation,
    Disease,
    DiseaseAlias,
    ExtractionMethod,
    Location,
    LocationAlias,
    ValidationStatus,
)


@dataclass(frozen=True)
class EntityMention:
    entity_id: str
    canonical_name: str
    matched_text: str
    start: int
    end: int
    mention_count: int = 1


@dataclass
class EntityExtractionResult:
    article: Article
    disease_mentions: list[EntityMention] = field(
        default_factory=list
    )
    location_mentions: list[EntityMention] = field(
        default_factory=list
    )
    diseases_created: int = 0
    diseases_updated: int = 0
    locations_created: int = 0
    locations_updated: int = 0


def normalize_for_matching(value: str) -> str:
    """
    Normalisasi ringan untuk pencocokan.

    Posisi karakter tidak boleh berubah terlalu jauh karena hasil
    pencocokan digunakan untuk mengambil mention_text dari artikel asli.
    """
    return value.replace("\xa0", " ")


def build_phrase_pattern(phrase: str) -> re.Pattern:
    """
    Membuat pola yang tidak mencocokkan bagian dari kata lain.

    Contoh:
    'DBD' cocok dengan 'kasus DBD meningkat',
    tetapi tidak cocok sebagai bagian dari kata yang lebih panjang.
    """
    escaped_phrase = re.escape(phrase.strip())

    return re.compile(
        rf"(?<!\w){escaped_phrase}(?!\w)",
        flags=re.IGNORECASE,
    )


def collect_phrase_matches(
    *,
    text: str,
    phrases: list[str],
) -> list[tuple[str, int, int]]:
    """
    Mengembalikan seluruh mention dan mengutamakan frasa yang lebih
    panjang agar 'Kabupaten Bandung' tidak dipotong menjadi 'Bandung'.
    """
    unique_phrases = {
        phrase.strip()
        for phrase in phrases
        if phrase and phrase.strip()
    }

    sorted_phrases = sorted(
        unique_phrases,
        key=len,
        reverse=True,
    )

    matches: list[tuple[str, int, int]] = []
    occupied_ranges: list[tuple[int, int]] = []

    for phrase in sorted_phrases:
        pattern = build_phrase_pattern(phrase)

        for match in pattern.finditer(text):
            start, end = match.span()

            overlaps = any(
                start < occupied_end and end > occupied_start
                for occupied_start, occupied_end in occupied_ranges
            )

            if overlaps:
                continue

            matches.append(
                (
                    text[start:end],
                    start,
                    end,
                )
            )

            occupied_ranges.append(
                (
                    start,
                    end,
                )
            )

    return sorted(
        matches,
        key=lambda item: item[1],
    )


def get_disease_phrases(
    disease: Disease,
) -> list[str]:
    phrases = [
        disease.name,
        disease.canonical_name,
    ]

    phrases.extend(
        disease.aliases.filter(
            is_active=True,
        ).values_list(
            "alias",
            flat=True,
        )
    )

    return [
        phrase
        for phrase in phrases
        if phrase
    ]


def get_location_phrases(
    location: Location,
) -> list[str]:
    phrases = [
        location.name,
    ]

    phrases.extend(
        location.aliases.filter(
            is_active=True,
        ).values_list(
            "alias",
            flat=True,
        )
    )

    return [
        phrase
        for phrase in phrases
        if phrase
    ]


def extract_disease_mentions(
    article: Article,
) -> list[EntityMention]:
    text = normalize_for_matching(
        f"{article.title}\n{article.content_text}"
    )

    mentions: list[EntityMention] = []

    diseases = Disease.objects.filter(
        is_active=True,
    ).prefetch_related(
        "aliases",
    )

    for disease in diseases:
        matches = collect_phrase_matches(
            text=text,
            phrases=get_disease_phrases(disease),
        )

        if not matches:
            continue

        first_match = matches[0]

        mentions.append(
            EntityMention(
                entity_id=str(disease.id),
                canonical_name=disease.name,
                matched_text=first_match[0],
                start=first_match[1],
                end=first_match[2],
                mention_count=len(matches),
            )
        )

    return mentions


def extract_location_mentions(
    article: Article,
) -> list[EntityMention]:
    text = normalize_for_matching(
        f"{article.title}\n{article.content_text}"
    )

    mentions: list[EntityMention] = []

    locations = Location.objects.filter(
        is_active=True,
    ).prefetch_related(
        "aliases",
    )

    for location in locations:
        matches = collect_phrase_matches(
            text=text,
            phrases=get_location_phrases(location),
        )

        if not matches:
            continue

        first_match = matches[0]

        mentions.append(
            EntityMention(
                entity_id=str(location.id),
                canonical_name=location.name,
                matched_text=first_match[0],
                start=first_match[1],
                end=first_match[2],
                mention_count=len(matches),
            )
        )

    return mentions


def calculate_rule_confidence(
    *,
    mention_count: int,
    found_in_title: bool,
) -> float:
    """
    Skor ini merupakan confidence teknis hasil pencocokan, bukan
    penilaian kredibilitas informasi intelijen.
    """
    score = 0.70

    if found_in_title:
        score += 0.15

    if mention_count > 1:
        score += min(
            0.05 * (mention_count - 1),
            0.15,
        )

    return min(score, 1.0)


@transaction.atomic
def extract_article_entities(
    article: Article,
) -> EntityExtractionResult:
    # combined_text = normalize_for_matching(
    #     f"{article.title}\n{article.content_text}"
    # )

    title_length = len(article.title)

    disease_mentions = extract_disease_mentions(
        article
    )
    location_mentions = extract_location_mentions(
        article
    )

    result = EntityExtractionResult(
        article=article,
        disease_mentions=disease_mentions,
        location_mentions=location_mentions,
    )

    for mention in disease_mentions:
        disease = Disease.objects.get(
            id=mention.entity_id,
        )

        confidence = calculate_rule_confidence(
            mention_count=mention.mention_count,
            found_in_title=mention.start <= title_length,
        )

        relation, created = ArticleDisease.objects.update_or_create(
            article=article,
            disease=disease,
            defaults={
                "mention_text": mention.matched_text,
                "confidence_score": confidence,
                "extraction_method": ExtractionMethod.RULE_BASED,
                "validation_status": ValidationStatus.UNREVIEWED,
            },
        )

        if created:
            result.diseases_created += 1
        else:
            result.diseases_updated += 1

    for mention in location_mentions:
        location = Location.objects.get(
            id=mention.entity_id,
        )

        confidence = calculate_rule_confidence(
            mention_count=mention.mention_count,
            found_in_title=mention.start <= title_length,
        )

        relation, created = ArticleLocation.objects.update_or_create(
            article=article,
            location=location,
            defaults={
                "mention_text": mention.matched_text,
                "confidence_score": confidence,
                "extraction_method": ExtractionMethod.RULE_BASED,
                "validation_status": ValidationStatus.UNREVIEWED,
            },
        )

        if created:
            result.locations_created += 1
        else:
            result.locations_updated += 1

    return result