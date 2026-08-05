import re
from dataclasses import dataclass, field

from django.db import transaction

from apps.articles.models import Article

from ..geolocation import (
    IndonesiaGeolocationResult,
    resolve_indonesia_locations,
)
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


AUTOMATED_LOCATION_METHODS = (
    ExtractionMethod.SYSTEM,
    ExtractionMethod.RULE_BASED,
)


@dataclass(frozen=True)
class EntityMention:
    entity_id: str
    canonical_name: str
    matched_text: str
    start: int
    end: int
    mention_count: int = 1
    confidence_score: float | None = None
    is_primary: bool = False
    administrative_level: str = ""
    country_code: str = ""
    code: str = ""
    parent_name: str = ""
    latitude: str | None = None
    longitude: str | None = None


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
    geolocation_scope: str = "unresolved"
    ambiguous_location_terms: tuple[str, ...] = ()

    @property
    def primary_location(self) -> EntityMention | None:
        return next(
            (
                mention
                for mention in self.location_mentions
                if mention.is_primary
            ),
            None,
        )

    def geolocation_metadata(self) -> dict:
        primary = self.primary_location

        return {
            "scope": self.geolocation_scope,
            "status": (
                "resolved"
                if primary
                and primary.latitude is not None
                and primary.longitude is not None
                and not self.ambiguous_location_terms
                else "needs_review"
            ),
            "primary_location": (
                {
                    "location_id": primary.entity_id,
                    "name": primary.canonical_name,
                    "code": primary.code,
                    "administrative_level": (
                        primary.administrative_level
                    ),
                    "parent_name": primary.parent_name,
                    "latitude": primary.latitude,
                    "longitude": primary.longitude,
                    "confidence_score": (
                        primary.confidence_score
                    ),
                    "is_mappable": bool(
                        primary.latitude is not None
                        and primary.longitude is not None
                    ),
                }
                if primary
                else None
            ),
            "ambiguous_terms": list(
                self.ambiguous_location_terms
            ),
        }


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


def _extract_location_geolocation(
    article: Article,
) -> tuple[list[EntityMention], IndonesiaGeolocationResult]:
    text = normalize_for_matching(
        f"{article.title}\n{article.content_text}"
    )
    geolocation = resolve_indonesia_locations(
        text,
        title_length=len(article.title),
    )
    mentions = [
        EntityMention(
            entity_id=item.location_id,
            canonical_name=item.location_name,
            matched_text=item.matched_text,
            start=item.start,
            end=item.end,
            mention_count=item.mention_count,
            confidence_score=item.confidence_score,
            is_primary=item.is_primary,
            administrative_level=(
                item.administrative_level
            ),
            country_code=item.country_code,
            code=item.code,
            parent_name=item.parent_name,
            latitude=item.latitude,
            longitude=item.longitude,
        )
        for item in geolocation.mentions
    ]

    return mentions, geolocation


def extract_location_mentions(
    article: Article,
) -> list[EntityMention]:
    mentions, _ = _extract_location_geolocation(
        article
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


def _primary_location_mentions(
    mentions: list[EntityMention],
) -> list[EntityMention]:
    return [
        mention
        for mention in mentions
        if mention.is_primary
    ]


def _persist_primary_location(
    *,
    article: Article,
    result: EntityExtractionResult,
    title_length: int,
) -> None:
    """Simpan hanya satu lokasi kejadian otomatis yang actionable."""
    location_mentions = result.location_mentions

    if not location_mentions:
        return

    protected_primary_exists = ArticleLocation.objects.filter(
        article=article,
        is_primary=True,
    ).exclude(
        extraction_method__in=AUTOMATED_LOCATION_METHODS,
        validation_status=ValidationStatus.UNREVIEWED,
    ).exists()

    automatic_domestic = ArticleLocation.objects.filter(
        article=article,
        location__country_code="ID",
        extraction_method__in=AUTOMATED_LOCATION_METHODS,
        validation_status=ValidationStatus.UNREVIEWED,
    )

    if protected_primary_exists:
        # Keputusan analis mengalahkan resolver otomatis. Relasi otomatis
        # yang belum ditinjau tidak perlu dipertahankan sebagai lokasi kedua.
        automatic_domestic.delete()
        return

    automatic_domestic.filter(
        is_primary=True,
    ).update(
        is_primary=False,
    )

    detected_location_ids: set[str] = set()

    for mention in location_mentions:
        location = Location.objects.get(
            id=mention.entity_id,
        )
        detected_location_ids.add(str(location.id))
        confidence = (
            mention.confidence_score
            if mention.confidence_score is not None
            else calculate_rule_confidence(
                mention_count=mention.mention_count,
                found_in_title=(
                    mention.start < title_length
                ),
            )
        )

        relation, created = ArticleLocation.objects.get_or_create(
            article=article,
            location=location,
            defaults={
                "mention_text": mention.matched_text,
                "confidence_score": confidence,
                "extraction_method": ExtractionMethod.RULE_BASED,
                "validation_status": ValidationStatus.UNREVIEWED,
                "is_primary": True,
            },
        )

        if created:
            result.locations_created += 1
        else:
            if (
                relation.extraction_method
                in AUTOMATED_LOCATION_METHODS
                and relation.validation_status
                == ValidationStatus.UNREVIEWED
            ):
                relation.mention_text = mention.matched_text
                relation.confidence_score = confidence
                relation.extraction_method = (
                    ExtractionMethod.RULE_BASED
                )
                relation.is_primary = True
                relation.save(
                    update_fields=[
                        "mention_text",
                        "confidence_score",
                        "extraction_method",
                        "is_primary",
                        "updated_at",
                    ]
                )

            result.locations_updated += 1

    automatic_domestic.exclude(
        location_id__in=detected_location_ids,
    ).delete()


@transaction.atomic
def extract_article_locations(
    article: Article,
) -> EntityExtractionResult:
    """Ekstrak ulang lokasi tanpa mengubah entitas penyakit."""
    all_mentions, geolocation = _extract_location_geolocation(
        article
    )
    result = EntityExtractionResult(
        article=article,
        location_mentions=_primary_location_mentions(
            all_mentions
        ),
        geolocation_scope=geolocation.scope,
        ambiguous_location_terms=(
            geolocation.ambiguous_terms
        ),
    )
    _persist_primary_location(
        article=article,
        result=result,
        title_length=len(article.title),
    )
    return result


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
    all_location_mentions, geolocation = _extract_location_geolocation(
        article
    )
    location_mentions = _primary_location_mentions(
        all_location_mentions
    )

    result = EntityExtractionResult(
        article=article,
        disease_mentions=disease_mentions,
        location_mentions=location_mentions,
        geolocation_scope=geolocation.scope,
        ambiguous_location_terms=(
            geolocation.ambiguous_terms
        ),
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

    _persist_primary_location(
        article=article,
        result=result,
        title_length=title_length,
    )

    return result
