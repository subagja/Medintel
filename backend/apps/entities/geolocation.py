from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Iterable

from .models import Location


AUTOMATED_LEVELS = (
    Location.AdministrativeLevel.PROVINCE,
    Location.AdministrativeLevel.REGENCY,
    Location.AdministrativeLevel.CITY,
)

LEVEL_SPECIFICITY = {
    Location.AdministrativeLevel.COUNTRY: 0,
    Location.AdministrativeLevel.PROVINCE: 1,
    Location.AdministrativeLevel.REGENCY: 2,
    Location.AdministrativeLevel.CITY: 2,
    Location.AdministrativeLevel.DISTRICT: 3,
    Location.AdministrativeLevel.VILLAGE: 4,
    Location.AdministrativeLevel.OTHER: 0,
}

ADMINISTRATIVE_MARKERS = (
    (
        re.compile(r"\bprovinsi\s*$", re.IGNORECASE),
        Location.AdministrativeLevel.PROVINCE,
    ),
    (
        re.compile(r"\b(?:kabupaten|kab\.?)\s*$", re.IGNORECASE),
        Location.AdministrativeLevel.REGENCY,
    ),
    (
        re.compile(r"\bkota\s*$", re.IGNORECASE),
        Location.AdministrativeLevel.CITY,
    ),
)

EVENT_ANCHOR_PATTERN = re.compile(
    r"\b(?:"
    r"kasus|pasien|penderita|suspek|terduga|kematian|meninggal|"
    r"terinfeksi|positif|dirawat|wabah|kejadian luar biasa|klb"
    r")\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class LocationCandidate:
    location_id: str
    location_name: str
    administrative_level: str
    country_code: str
    code: str
    parent_id: str | None
    parent_name: str
    latitude: str | None
    longitude: str | None
    canonical_term: str
    is_alias: bool


@dataclass(frozen=True)
class LocationSearchTerm:
    term: str
    candidates: tuple[LocationCandidate, ...]


@dataclass(frozen=True)
class ResolvedLocationMention:
    location_id: str
    location_name: str
    administrative_level: str
    country_code: str
    code: str
    parent_name: str
    latitude: str | None
    longitude: str | None
    matched_text: str
    start: int
    end: int
    mention_count: int
    confidence_score: float
    is_primary: bool = False

    @property
    def is_mappable(self) -> bool:
        return bool(
            self.latitude is not None
            and self.longitude is not None
        )


@dataclass(frozen=True)
class IndonesiaGeolocationResult:
    mentions: tuple[ResolvedLocationMention, ...]
    ambiguous_terms: tuple[str, ...]
    scope: str

    @property
    def primary(self) -> ResolvedLocationMention | None:
        return next(
            (
                mention
                for mention in self.mentions
                if mention.is_primary
            ),
            None,
        )

    @property
    def needs_review(self) -> bool:
        primary = self.primary

        return bool(
            primary is None
            or self.ambiguous_terms
            or primary.confidence_score < 0.75
            or not primary.is_mappable
        )

    def as_metadata(self) -> dict:
        primary = self.primary

        return {
            "scope": self.scope,
            "status": (
                "needs_review"
                if self.needs_review
                else "resolved"
            ),
            "primary_location": (
                {
                    "location_id": primary.location_id,
                    "name": primary.location_name,
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
                    "is_mappable": primary.is_mappable,
                }
                if primary
                else None
            ),
            "resolved_locations": [
                {
                    "location_id": item.location_id,
                    "name": item.location_name,
                    "administrative_level": (
                        item.administrative_level
                    ),
                    "matched_text": item.matched_text,
                    "confidence_score": item.confidence_score,
                    "is_primary": item.is_primary,
                    "is_mappable": item.is_mappable,
                }
                for item in self.mentions
            ],
            "ambiguous_terms": list(self.ambiguous_terms),
        }


def normalize_location_term(value: str) -> str:
    normalized = (value or "").casefold()
    normalized = re.sub(
        r"[^\w\s]+",
        " ",
        normalized,
        flags=re.UNICODE,
    )
    return " ".join(normalized.split())


def _location_pattern(term: str) -> re.Pattern:
    tokens = re.findall(
        r"\w+",
        term,
        flags=re.UNICODE,
    )

    if not tokens:
        return re.compile(r"(?!x)x")

    separator = r"(?:[\s.\-/,()]*)"

    return re.compile(
        r"(?<!\w)"
        + separator.join(
            re.escape(token)
            for token in tokens
        )
        + r"(?!\w)",
        flags=re.IGNORECASE,
    )


def _automatic_terms(location: Location) -> set[str]:
    terms = {location.name.strip()}
    name = location.name.strip()

    prefix_variants = (
        ("Kabupaten ", ("Kab ", "Kab. ")),
        ("Provinsi ", ()),
        ("Kota ", ()),
    )

    for prefix, abbreviated_prefixes in prefix_variants:
        if not name.casefold().startswith(prefix.casefold()):
            continue

        suffix = name[len(prefix):].strip()

        if suffix:
            terms.add(suffix)

            for abbreviated_prefix in abbreviated_prefixes:
                terms.add(
                    f"{abbreviated_prefix}{suffix}"
                )

        break

    return {
        term
        for term in terms
        if normalize_location_term(term)
    }


@lru_cache(maxsize=1)
def get_indonesia_location_index() -> tuple[
    LocationSearchTerm,
    ...,
]:
    locations = list(
        Location.objects.filter(
            is_active=True,
            country_code="ID",
            administrative_level__in=AUTOMATED_LEVELS,
        )
        .select_related("parent")
        .prefetch_related("aliases")
        .order_by(
            "administrative_level",
            "name",
        )
    )

    terms_by_value: dict[
        str,
        list[LocationCandidate],
    ] = {}

    for location in locations:
        canonical_terms = _automatic_terms(location)
        alias_terms = set(
            location.aliases.filter(
                is_active=True,
            ).values_list(
                "alias",
                flat=True,
            )
        )

        for term, is_alias in (
            *((item, False) for item in canonical_terms),
            *((item, True) for item in alias_terms),
        ):
            normalized_term = normalize_location_term(term)

            if len(normalized_term) < 3:
                continue

            candidate = LocationCandidate(
                location_id=str(location.id),
                location_name=location.name,
                administrative_level=(
                    location.administrative_level
                ),
                country_code=location.country_code,
                code=location.code,
                parent_id=(
                    str(location.parent_id)
                    if location.parent_id
                    else None
                ),
                parent_name=(
                    location.parent.name
                    if location.parent
                    else ""
                ),
                latitude=(
                    str(location.latitude)
                    if location.latitude is not None
                    else None
                ),
                longitude=(
                    str(location.longitude)
                    if location.longitude is not None
                    else None
                ),
                canonical_term=term,
                is_alias=is_alias,
            )

            bucket = terms_by_value.setdefault(
                normalized_term,
                [],
            )

            if candidate not in bucket:
                bucket.append(candidate)

    rows = [
        LocationSearchTerm(
            term=term,
            candidates=tuple(candidates),
        )
        for term, candidates in terms_by_value.items()
    ]
    rows.sort(
        key=lambda item: len(item.term),
        reverse=True,
    )

    return tuple(rows)


def clear_geolocation_cache() -> None:
    get_indonesia_location_index.cache_clear()


def infer_event_anchor_spans(
    text: str,
) -> tuple[tuple[int, int], ...]:
    return tuple(
        match.span()
        for match in EVENT_ANCHOR_PATTERN.finditer(text)
    )


def _expected_level(
    *,
    text: str,
    start: int,
) -> str | None:
    prefix = text[
        max(0, start - 18):start
    ]

    for pattern, level in ADMINISTRATIVE_MARKERS:
        if pattern.search(prefix):
            return level

    return None


def _distance_to_spans(
    *,
    start: int,
    end: int,
    spans: Iterable[tuple[int, int]],
) -> int | None:
    distances: list[int] = []

    for span_start, span_end in spans:
        if start <= span_end and end >= span_start:
            distances.append(0)
        elif end < span_start:
            distances.append(span_start - end)
        else:
            distances.append(start - span_end)

    return min(distances) if distances else None


def _parent_is_in_context(
    *,
    candidate: LocationCandidate,
    text: str,
    start: int,
    end: int,
) -> bool:
    if not candidate.parent_name:
        return False

    context = text[
        max(0, start - 140):min(len(text), end + 140)
    ]

    return bool(
        _location_pattern(
            candidate.parent_name
        ).search(context)
    )


def _resolve_candidate(
    *,
    candidates: tuple[LocationCandidate, ...],
    text: str,
    matched_text: str,
    start: int,
    end: int,
) -> LocationCandidate | None:
    by_location = {
        candidate.location_id: candidate
        for candidate in candidates
    }
    remaining = list(by_location.values())

    if len(remaining) == 1:
        return remaining[0]

    expected_level = _expected_level(
        text=text,
        start=start,
    )

    if expected_level:
        level_matches = [
            candidate
            for candidate in remaining
            if candidate.administrative_level == expected_level
        ]

        if len(level_matches) == 1:
            return level_matches[0]

        if level_matches:
            remaining = level_matches

    normalized_match = normalize_location_term(
        matched_text
    )
    exact_name_matches = [
        candidate
        for candidate in remaining
        if normalize_location_term(
            candidate.location_name
        ) == normalized_match
    ]

    if len(exact_name_matches) == 1:
        return exact_name_matches[0]

    parent_matches = [
        candidate
        for candidate in remaining
        if _parent_is_in_context(
            candidate=candidate,
            text=text,
            start=start,
            end=end,
        )
    ]

    if len(parent_matches) == 1:
        return parent_matches[0]

    return None


def _calculate_confidence(
    *,
    candidate: LocationCandidate,
    matched_text: str,
    start: int,
    end: int,
    title_length: int,
    anchor_spans: tuple[tuple[int, int], ...],
    text: str,
    mention_count: int,
) -> float:
    score = 0.62
    normalized_match = normalize_location_term(
        matched_text
    )

    if normalize_location_term(
        candidate.location_name
    ) == normalized_match:
        score += 0.08

    if _expected_level(
        text=text,
        start=start,
    ) == candidate.administrative_level:
        score += 0.10

    if start <= title_length:
        score += 0.08

    distance = _distance_to_spans(
        start=start,
        end=end,
        spans=anchor_spans,
    )

    if distance is not None and distance <= 300:
        score += 0.08
    elif distance is not None and distance <= 900:
        score += 0.04

    if (
        candidate.latitude is not None
        and candidate.longitude is not None
    ):
        score += 0.02

    if mention_count > 1:
        score += min(
            0.02 * (mention_count - 1),
            0.04,
        )

    return min(round(score, 4), 1.0)


def resolve_indonesia_locations(
    text: str,
    *,
    title_length: int = 0,
    anchor_spans: Iterable[tuple[int, int]] = (),
) -> IndonesiaGeolocationResult:
    source_text = (text or "").replace("\xa0", " ")
    provided_anchor_spans = tuple(anchor_spans)
    event_anchor_spans = (
        provided_anchor_spans
        or infer_event_anchor_spans(source_text)
    )

    raw_matches: list[
        tuple[int, int, str, tuple[LocationCandidate, ...]]
    ] = []

    for search_term in get_indonesia_location_index():
        pattern = _location_pattern(search_term.term)

        for match in pattern.finditer(source_text):
            raw_matches.append(
                (
                    match.start(),
                    match.end(),
                    match.group(0),
                    search_term.candidates,
                )
            )

    raw_matches.sort(
        key=lambda item: (
            -(item[1] - item[0]),
            item[0],
        )
    )

    occupied: list[tuple[int, int]] = []
    resolved_rows: list[
        tuple[LocationCandidate, str, int, int]
    ] = []
    ambiguous_terms: list[str] = []

    for start, end, matched_text, candidates in raw_matches:
        if any(
            start < used_end and end > used_start
            for used_start, used_end in occupied
        ):
            continue

        candidate = _resolve_candidate(
            candidates=candidates,
            text=source_text,
            matched_text=matched_text,
            start=start,
            end=end,
        )

        occupied.append((start, end))

        if candidate is None:
            normalized_ambiguous = matched_text.strip()

            if (
                normalized_ambiguous
                and normalized_ambiguous not in ambiguous_terms
            ):
                ambiguous_terms.append(normalized_ambiguous)

            continue

        resolved_rows.append(
            (
                candidate,
                matched_text,
                start,
                end,
            )
        )

    grouped: dict[
        str,
        list[tuple[LocationCandidate, str, int, int]],
    ] = {}

    for row in resolved_rows:
        grouped.setdefault(
            row[0].location_id,
            [],
        ).append(row)

    mentions: list[ResolvedLocationMention] = []

    for rows in grouped.values():
        rows.sort(
            key=lambda row: (
                _distance_to_spans(
                    start=row[2],
                    end=row[3],
                    spans=event_anchor_spans,
                )
                if event_anchor_spans
                else 10**9,
                row[2],
            )
        )
        candidate, matched_text, start, end = rows[0]
        mention_count = len(rows)
        confidence = _calculate_confidence(
            candidate=candidate,
            matched_text=matched_text,
            start=start,
            end=end,
            title_length=title_length,
            anchor_spans=event_anchor_spans,
            text=source_text,
            mention_count=mention_count,
        )

        mentions.append(
            ResolvedLocationMention(
                location_id=candidate.location_id,
                location_name=candidate.location_name,
                administrative_level=(
                    candidate.administrative_level
                ),
                country_code=candidate.country_code,
                code=candidate.code,
                parent_name=candidate.parent_name,
                latitude=candidate.latitude,
                longitude=candidate.longitude,
                matched_text=matched_text,
                start=start,
                end=end,
                mention_count=mention_count,
                confidence_score=confidence,
            )
        )

    if not mentions:
        return IndonesiaGeolocationResult(
            mentions=(),
            ambiguous_terms=tuple(ambiguous_terms),
            scope="unresolved",
        )

    def primary_key(
        mention: ResolvedLocationMention,
    ) -> tuple:
        distance = _distance_to_spans(
            start=mention.start,
            end=mention.end,
            spans=event_anchor_spans,
        )

        return (
            -(
                distance
                if distance is not None
                else 10**9
            ),
            mention.start <= title_length,
            LEVEL_SPECIFICITY.get(
                mention.administrative_level,
                0,
            ),
            mention.confidence_score,
            -mention.start,
        )

    primary = max(
        mentions,
        key=primary_key,
    )
    mentions = [
        replace(
            mention,
            is_primary=(
                mention.location_id
                == primary.location_id
            ),
        )
        for mention in mentions
    ]
    mentions.sort(
        key=lambda item: (
            not item.is_primary,
            item.start,
        )
    )

    return IndonesiaGeolocationResult(
        mentions=tuple(mentions),
        ambiguous_terms=tuple(ambiguous_terms),
        scope="domestic",
    )
