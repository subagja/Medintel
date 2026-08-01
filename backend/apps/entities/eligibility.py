from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from apps.entities.models import (
    Disease,
    Location,
    SurveillanceProgram,
)


SCALED_NUMBER_PATTERN = (
    r"(?P<count>"
    r"(?:\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)"
    r")"
    r"\s*(?P<multiplier>ribu|juta|miliar)?"
)

COUNT_PATTERNS = (
    re.compile(
        rf"\b{SCALED_NUMBER_PATTERN}\s+"
        r"(?:kasus|pasien|penderita|korban|kematian|"
        r"orang\s+terinfeksi|orang\s+positif|orang\s+meninggal)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:sebanyak|tercatat|ditemukan|mencapai|sekitar|"
        rf"melaporkan|mencatat)\s+{SCALED_NUMBER_PATTERN}\s+"
        r"(?:kasus|pasien|penderita|korban|kematian|orang)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"\bjumlah\s+(?:kasus|pasien|penderita|kematian)\s+"
        rf"(?:mencapai|menjadi|sebanyak|sekitar)?\s*"
        rf"{SCALED_NUMBER_PATTERN}\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:kasus|pasien|penderita|kematian)\s+"
        rf"[\wÀ-ÿ/-]+(?:\s+[\wÀ-ÿ/-]+){{0,8}}\s+"
        rf"(?:sebanyak|mencapai|sekitar|tercatat)\s+"
        rf"{SCALED_NUMBER_PATTERN}\b",
        flags=re.IGNORECASE,
    ),
)


NUMBER_MULTIPLIERS = {
    "ribu": 1_000,
    "juta": 1_000_000,
    "miliar": 1_000_000_000,
}


@dataclass(frozen=True)
class DiseaseMention:
    disease_id: str
    disease_name: str
    matched_text: str
    start: int
    end: int


@dataclass(frozen=True)
class CountMention:
    value: int
    matched_text: str
    start: int
    end: int


@dataclass(frozen=True)
class LocationMention:
    location_id: str
    location_name: str
    matched_text: str
    start: int
    end: int


@dataclass(frozen=True)
class CheapFilterResult:
    passed: bool
    disease_mentions: tuple[DiseaseMention, ...]
    count_mentions: tuple[CountMention, ...]
    reason: str


@dataclass(frozen=True)
class SurveillanceEligibilityResult:
    is_eligible: bool
    disease_mentions: tuple[DiseaseMention, ...]
    count_mentions: tuple[CountMention, ...]
    location_mentions: tuple[LocationMention, ...]
    evidence_text: str
    reason: str


def normalize_text(value: str) -> str:
    """
    Normalisasi untuk disease, count, location, dan pengukuran konteks.

    Titik dan koma dipertahankan agar angka seperti 1.538 dan
    1,5 juta tidak berubah menjadi "1 538" atau "1 5 juta".
    Semua extractor memakai teks normalisasi yang sama sehingga
    posisi mention tetap berada pada sistem koordinat yang sama.
    """
    normalized = (value or "").casefold()

    normalized = re.sub(
        r"[^\w\s.,]+",
        " ",
        normalized,
        flags=re.UNICODE,
    )

    normalized = re.sub(
        r"\s+",
        " ",
        normalized,
    )

    return normalized.strip()


def _phrase_pattern(term: str) -> re.Pattern:
    return re.compile(
        r"(?<!\w)"
        + re.escape(term)
        + r"(?!\w)",
        flags=re.IGNORECASE,
    )


@lru_cache(maxsize=1)
def get_disease_term_index() -> tuple[
    tuple[str, str, str],
    ...,
]:
    rows: list[tuple[str, str, str]] = []

    diseases = (
        Disease.objects.filter(
            is_active=True,
            surveillance_memberships__is_active=True,
            surveillance_memberships__program__status=(
                SurveillanceProgram.Status.ACTIVE
            ),
        )
        .distinct()
        .prefetch_related("aliases")
        .order_by("name")
    )

    for disease in diseases:
        terms = {disease.name}

        if disease.canonical_name:
            terms.add(disease.canonical_name)

        terms.update(
            disease.aliases.filter(
                is_active=True,
            ).values_list(
                "alias",
                flat=True,
            )
        )

        for term in terms:
            normalized = normalize_text(term)

            if len(normalized) < 3:
                continue

            rows.append(
                (
                    normalized,
                    str(disease.id),
                    disease.name,
                )
            )

    rows.sort(
        key=lambda item: len(item[0]),
        reverse=True,
    )

    return tuple(rows)


@lru_cache(maxsize=1)
def get_location_term_index() -> tuple[
    tuple[str, str, str],
    ...,
]:
    rows: list[tuple[str, str, str]] = []

    locations = (
        Location.objects.filter(
            is_active=True,
        )
        .prefetch_related("aliases")
        .order_by("name")
    )

    for location in locations:
        terms = {location.name}

        terms.update(
            location.aliases.filter(
                is_active=True,
            ).values_list(
                "alias",
                flat=True,
            )
        )

        for term in terms:
            normalized = normalize_text(term)

            if len(normalized) < 3:
                continue

            rows.append(
                (
                    normalized,
                    str(location.id),
                    location.name,
                )
            )

    rows.sort(
        key=lambda item: len(item[0]),
        reverse=True,
    )

    return tuple(rows)


def clear_eligibility_caches() -> None:
    """
    Dipanggil bila master penyakit/lokasi/alias berubah saat proses aktif.
    Pada command crawler baru, cache otomatis baru saat proses berikutnya.
    """
    get_disease_term_index.cache_clear()
    get_location_term_index.cache_clear()


def extract_disease_mentions(
    text: str,
) -> tuple[DiseaseMention, ...]:
    normalized_text = normalize_text(text)
    mentions: list[DiseaseMention] = []
    occupied: list[tuple[int, int]] = []

    for term, disease_id, disease_name in (
        get_disease_term_index()
    ):
        match = _phrase_pattern(term).search(
            normalized_text
        )

        if match is None:
            continue

        start, end = match.span()

        if any(
            start < used_end and end > used_start
            for used_start, used_end in occupied
        ):
            continue

        mentions.append(
            DiseaseMention(
                disease_id=disease_id,
                disease_name=disease_name,
                matched_text=match.group(0),
                start=start,
                end=end,
            )
        )
        occupied.append((start, end))

    return tuple(
        sorted(
            mentions,
            key=lambda item: item.start,
        )
    )


def parse_scaled_count(
    raw_value: str,
    multiplier: str | None,
) -> int:
    raw = raw_value.strip()
    multiplier_key = (
        multiplier.casefold()
        if multiplier
        else None
    )

    if multiplier_key:
        # Bentuk desimal berskala:
        # 1,5 juta / 1.5 juta.
        decimal_value = float(
            raw.replace(",", ".")
        )
        return int(
            decimal_value
            * NUMBER_MULTIPLIERS[multiplier_key]
        )

    # Tanpa multiplier, titik dan koma diperlakukan
    # sebagai pemisah ribuan: 1.538 / 1,538.
    return int(
        raw.replace(".", "").replace(",", "")
    )


def extract_count_mentions(
    text: str,
) -> tuple[CountMention, ...]:
    normalized_text = normalize_text(text)
    mentions: list[CountMention] = []
    occupied: list[tuple[int, int]] = []

    for pattern in COUNT_PATTERNS:
        for match in pattern.finditer(
            normalized_text
        ):
            start, end = match.span()

            if any(
                start < used_end and end > used_start
                for used_start, used_end in occupied
            ):
                continue

            try:
                value = parse_scaled_count(
                    match.group("count"),
                    match.groupdict().get("multiplier"),
                )
            except (
                ValueError,
                KeyError,
            ):
                continue

            mentions.append(
                CountMention(
                    value=value,
                    matched_text=match.group(0),
                    start=start,
                    end=end,
                )
            )
            occupied.append((start, end))

    return tuple(
        sorted(
            mentions,
            key=lambda item: item.start,
        )
    )


def extract_location_mentions(
    text: str,
) -> tuple[LocationMention, ...]:
    normalized_text = normalize_text(text)
    mentions: list[LocationMention] = []
    occupied: list[tuple[int, int]] = []

    for term, location_id, location_name in (
        get_location_term_index()
    ):
        match = _phrase_pattern(term).search(
            normalized_text
        )

        if match is None:
            continue

        start, end = match.span()

        if any(
            start < used_end and end > used_start
            for used_start, used_end in occupied
        ):
            continue

        mentions.append(
            LocationMention(
                location_id=location_id,
                location_name=location_name,
                matched_text=match.group(0),
                start=start,
                end=end,
            )
        )
        occupied.append((start, end))

    return tuple(
        sorted(
            mentions,
            key=lambda item: item.start,
        )
    )


def evaluate_cheap_filter(
    *,
    title: str,
    content: str,
    preview_chars: int = 5000,
) -> CheapFilterResult:
    preview = (
        f"{title}\n"
        f"{content[:preview_chars]}"
    ).strip()

    diseases = extract_disease_mentions(
        preview
    )

    if not diseases:
        return CheapFilterResult(
            passed=False,
            disease_mentions=(),
            count_mentions=(),
            reason=(
                "Filter awal: penyakit surveilans "
                "tidak ditemukan."
            ),
        )

    counts = extract_count_mentions(
        preview
    )

    if not counts:
        return CheapFilterResult(
            passed=False,
            disease_mentions=diseases,
            count_mentions=(),
            reason=(
                "Filter awal: penyakit ditemukan, tetapi "
                "jumlah kasus/pasien/kematian tidak ditemukan."
            ),
        )

    return CheapFilterResult(
        passed=True,
        disease_mentions=diseases,
        count_mentions=counts,
        reason=(
            "Filter awal lolos: penyakit dan jumlah ditemukan."
        ),
    )


def _build_evidence(
    *,
    normalized_text: str,
    start: int,
    end: int,
    radius: int = 350,
) -> str:
    left = max(0, start - radius)
    right = min(
        len(normalized_text),
        end + radius,
    )

    return normalized_text[left:right].strip()[:900]


def evaluate_surveillance_eligibility(
    *,
    title: str,
    content: str,
    cheap_result: CheapFilterResult | None = None,
    max_context_distance: int = 900,
) -> SurveillanceEligibilityResult:
    if cheap_result is None:
        cheap_result = evaluate_cheap_filter(
            title=title,
            content=content,
        )

    if not cheap_result.passed:
        return SurveillanceEligibilityResult(
            is_eligible=False,
            disease_mentions=(
                cheap_result.disease_mentions
            ),
            count_mentions=(
                cheap_result.count_mentions
            ),
            location_mentions=(),
            evidence_text="",
            reason=cheap_result.reason,
        )

    full_text = f"{title}\n{content}".strip()
    normalized_text = normalize_text(full_text)

    diseases = extract_disease_mentions(
        normalized_text
    )
    counts = extract_count_mentions(
        normalized_text
    )
    locations = extract_location_mentions(
        normalized_text
    )

    if not locations:
        return SurveillanceEligibilityResult(
            is_eligible=False,
            disease_mentions=diseases,
            count_mentions=counts,
            location_mentions=(),
            evidence_text="",
            reason=(
                "Filter lokasi: lokasi kejadian "
                "tidak teridentifikasi."
            ),
        )

    best_span: tuple[int, int] | None = None
    best_distance: int | None = None

    for disease in diseases:
        for count in counts:
            for location in locations:
                left = min(
                    disease.start,
                    count.start,
                    location.start,
                )
                right = max(
                    disease.end,
                    count.end,
                    location.end,
                )
                distance = right - left

                if (
                    best_distance is None
                    or distance < best_distance
                ):
                    best_distance = distance
                    best_span = (left, right)

    if (
        best_span is None
        or best_distance is None
        or best_distance > max_context_distance
    ):
        return SurveillanceEligibilityResult(
            is_eligible=False,
            disease_mentions=diseases,
            count_mentions=counts,
            location_mentions=locations,
            evidence_text="",
            reason=(
                "Penyakit, jumlah, dan lokasi ditemukan, "
                "tetapi tidak berada dalam konteks yang dekat."
            ),
        )

    return SurveillanceEligibilityResult(
        is_eligible=True,
        disease_mentions=diseases,
        count_mentions=counts,
        location_mentions=locations,
        evidence_text=_build_evidence(
            normalized_text=normalized_text,
            start=best_span[0],
            end=best_span[1],
        ),
        reason=(
            "Artikel memuat penyakit, jumlah kejadian, "
            "dan lokasi dalam konteks yang berdekatan."
        ),
    )
