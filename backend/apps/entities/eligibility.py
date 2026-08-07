from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from apps.locations.geolocation import (
    clear_geolocation_cache,
    resolve_indonesia_locations,
)
from apps.entities.models import (
    Disease,
    SurveillanceProgram,
)
from apps.locations.models import Location


SCALED_NUMBER_PATTERN = (
    r"(?P<count>"
    r"(?:"
    r"\d{1,3}(?:[.,]\d{3})+|"
    r"\d+(?:[.,]\d+)?|"
    r"(?:nol|satu|seorang|dua|tiga|empat|lima|enam|tujuh|"
    r"delapan|sembilan|sepuluh|sebelas|seratus|seribu|"
    r"sejuta|semiliar|belas|puluh|ratus|ribu|juta|miliar)"
    r"(?:\s+(?:nol|satu|dua|tiga|empat|lima|enam|tujuh|"
    r"delapan|sembilan|sepuluh|sebelas|belas|puluh|ratus|"
    r"ribu|juta|miliar)){0,20}"
    r")"
    r")"
    r"\s*(?P<multiplier>ribu|juta|miliar)?"
)

COUNT_METRIC_PATTERN = (
    r"(?P<metric>"
    r"kasus\s+(?:suspek|dugaan)|"
    r"orang\s+(?:diduga\s+terinfeksi|terinfeksi|positif|meninggal)|"
    r"kasus|pasien|penderita|suspek|terduga|korban|kematian|orang"
    r")"
)

COUNT_PATTERNS = (
    re.compile(
        rf"\b{SCALED_NUMBER_PATTERN}\s+"
        rf"{COUNT_METRIC_PATTERN}\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:sebanyak|tercatat|ditemukan|mencapai|sekitar|"
        rf"melaporkan|mencatat)\s+{SCALED_NUMBER_PATTERN}\s+"
        rf"{COUNT_METRIC_PATTERN}\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"\bjumlah\s+{COUNT_METRIC_PATTERN}\s+"
        rf"(?:mencapai|menjadi|sebanyak|sekitar)?\s*"
        rf"{SCALED_NUMBER_PATTERN}\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"\b{COUNT_METRIC_PATTERN}\s+"
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

INDONESIAN_UNIT_VALUES = {
    "nol": 0,
    "satu": 1,
    "dua": 2,
    "tiga": 3,
    "empat": 4,
    "lima": 5,
    "enam": 6,
    "tujuh": 7,
    "delapan": 8,
    "sembilan": 9,
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
    metric_type: str = "reported_event"


@dataclass(frozen=True)
class LocationMention:
    location_id: str
    location_name: str
    matched_text: str
    start: int
    end: int
    administrative_level: str = ""
    country_code: str = "ID"
    confidence_score: float = 0.0
    is_primary: bool = False
    latitude: str | None = None
    longitude: str | None = None


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
    clear_geolocation_cache()


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


def _parse_under_thousand(
    tokens: list[str],
) -> int:
    if not tokens:
        raise ValueError("Bilangan kata kosong.")

    value = 0
    remaining = list(tokens)

    if remaining[0] == "seratus":
        value = 100
        remaining = remaining[1:]
    elif (
        len(remaining) >= 2
        and remaining[0] in INDONESIAN_UNIT_VALUES
        and 1 <= INDONESIAN_UNIT_VALUES[remaining[0]] <= 9
        and remaining[1] == "ratus"
    ):
        value = (
            INDONESIAN_UNIT_VALUES[remaining[0]]
            * 100
        )
        remaining = remaining[2:]

    if not remaining:
        return value

    if len(remaining) == 1:
        token = remaining[0]

        if token in INDONESIAN_UNIT_VALUES:
            return value + INDONESIAN_UNIT_VALUES[token]

        if token == "sepuluh":
            return value + 10

        if token == "sebelas":
            return value + 11

        raise ValueError("Susunan bilangan kata tidak valid.")

    first_value = INDONESIAN_UNIT_VALUES.get(
        remaining[0]
    )

    if (
        len(remaining) == 2
        and first_value is not None
        and 2 <= first_value <= 9
        and remaining[1] == "belas"
    ):
        return value + 10 + first_value

    if (
        first_value is not None
        and 2 <= first_value <= 9
        and remaining[1] == "puluh"
    ):
        tens_value = first_value * 10

        if len(remaining) == 2:
            return value + tens_value

        if (
            len(remaining) == 3
            and remaining[2] in INDONESIAN_UNIT_VALUES
            and 1 <= INDONESIAN_UNIT_VALUES[remaining[2]] <= 9
        ):
            return (
                value
                + tens_value
                + INDONESIAN_UNIT_VALUES[remaining[2]]
            )

    raise ValueError("Susunan bilangan kata tidak valid.")


def parse_indonesian_number_words(
    raw_value: str,
) -> int:
    normalized = normalize_text(raw_value)
    tokens = normalized.split()

    replacements = {
        "seorang": ("satu",),
        "seribu": ("satu", "ribu"),
        "sejuta": ("satu", "juta"),
        "semiliar": ("satu", "miliar"),
    }
    expanded_tokens: list[str] = []

    for token in tokens:
        expanded_tokens.extend(
            replacements.get(token, (token,))
        )

    total = 0
    remaining = expanded_tokens

    for scale, multiplier_value in (
        ("miliar", 1_000_000_000),
        ("juta", 1_000_000),
        ("ribu", 1_000),
    ):
        if scale not in remaining:
            continue

        if remaining.count(scale) != 1:
            raise ValueError("Skala bilangan berulang.")

        scale_index = remaining.index(scale)
        scale_tokens = remaining[:scale_index]

        if not scale_tokens:
            raise ValueError("Skala bilangan tanpa nilai.")

        total += (
            _parse_under_thousand(scale_tokens)
            * multiplier_value
        )
        remaining = remaining[scale_index + 1:]

    if remaining:
        total += _parse_under_thousand(remaining)

    return total


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

    if not re.search(r"\d", raw):
        if multiplier_key:
            raise ValueError(
                "Bilangan kata tidak memakai multiplier terpisah."
            )

        return parse_indonesian_number_words(raw)

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


def classify_count_metric(raw_metric: str) -> str:
    normalized = normalize_text(raw_metric)

    if any(
        term in normalized
        for term in (
            "suspek",
            "terduga",
            "diduga",
            "dugaan",
        )
    ):
        return "suspect"

    if any(
        term in normalized
        for term in (
            "kematian",
            "meninggal",
        )
    ):
        return "death"

    if any(
        term in normalized
        for term in (
            "pasien",
            "penderita",
        )
    ):
        return "patient"

    if any(
        term in normalized
        for term in (
            "kasus",
            "terinfeksi",
            "positif",
        )
    ):
        return "confirmed_case"

    if normalized == "korban":
        return "affected_person"

    return "reported_event"


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
                    metric_type=classify_count_metric(
                        match.group("metric")
                    ),
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
    *,
    title_length: int = 0,
    anchor_spans: tuple[tuple[int, int], ...] = (),
) -> tuple[LocationMention, ...]:
    result = resolve_indonesia_locations(
        text,
        title_length=title_length,
        anchor_spans=anchor_spans,
    )

    return tuple(
        LocationMention(
            location_id=item.location_id,
            location_name=item.location_name,
            matched_text=item.matched_text,
            start=item.start,
            end=item.end,
            administrative_level=(
                item.administrative_level
            ),
            country_code=item.country_code,
            confidence_score=item.confidence_score,
            is_primary=item.is_primary,
            latitude=item.latitude,
            longitude=item.longitude,
        )
        for item in result.mentions
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
                "jumlah kasus/pasien/suspek/kematian "
                "tidak ditemukan."
            ),
        )

    return CheapFilterResult(
        passed=True,
        disease_mentions=diseases,
        count_mentions=counts,
        reason=(
            "Filter awal lolos: penyakit dan jumlah kejadian "
            "ditemukan."
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
    # Radius kedekatan antar penyakit-angka-lokasi. Dinaikkan dari 900 ->
    # 1500 karena artikel berita Indonesia yang lebih panjang sering
    # menyebut lokasi kejadian di paragraf pembuka sementara angka kasus
    # baru muncul beberapa paragraf kemudian, sehingga banyak artikel
    # relevan gagal lolos dengan radius yang terlalu ketat.
    max_context_distance: int = 1500,
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
    anchor_spans = tuple(
        (mention.start, mention.end)
        for mention in (*diseases, *counts)
    )
    locations = extract_location_mentions(
        normalized_text,
        title_length=len(normalize_text(title)),
        anchor_spans=anchor_spans,
    )

    if not locations:
        return SurveillanceEligibilityResult(
            is_eligible=False,
            disease_mentions=diseases,
            count_mentions=counts,
            location_mentions=(),
            evidence_text="",
            reason=(
                "Filter lokasi: lokasi kejadian Indonesia "
                "belum dapat dinormalisasi."
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
            "dan lokasi Indonesia dalam konteks yang berdekatan."
        ),
    )
