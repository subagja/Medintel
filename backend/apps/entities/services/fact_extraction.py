import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone

from apps.articles.models import Article

from apps.locations.models import Location

from ..models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ExtractionMethod,
    ValidationStatus,
)


NUMBER_WORDS = {
    "nol": 0,
    "satu": 1,
    "seorang": 1,
    "dua": 2,
    "tiga": 3,
    "empat": 4,
    "lima": 5,
    "enam": 6,
    "tujuh": 7,
    "delapan": 8,
    "sembilan": 9,
    "sepuluh": 10,
    "sebelas": 11,
    "belasan": 11,
    "puluhan": 20,
    "ratusan": 100,
    "ribuan": 1000,
}


MONTHS_ID = {
    "januari": 1,
    "februari": 2,
    "maret": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "agustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "desember": 12,
}


CASE_PATTERNS = [
    re.compile(
        r"\b(?P<count>\d[\d.]*)\s+kasus\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\bsebanyak\s+(?P<count>\d[\d.]*)\s+(?:kasus|orang|pasien)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\bjumlah\s+kasus\s+(?:mencapai|menjadi|sebanyak)?\s*"
        r"(?P<count>\d[\d.]*)\b",
        flags=re.IGNORECASE,
    ),
]


DEATH_PATTERNS = [
    re.compile(
        r"\b(?P<count>\d[\d.]*)\s+"
        r"(?:orang|pasien|korban)?\s*"
        r"(?:meninggal|tewas)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\bkematian\s+(?:mencapai|sebanyak)?\s*"
        r"(?P<count>\d[\d.]*)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<count>\d[\d.]*)\s+kematian\b",
        flags=re.IGNORECASE,
    ),
]


RECOVERY_PATTERNS = [
    re.compile(
        r"\b(?P<count>\d[\d.]*)\s+"
        r"(?:orang|pasien)?\s*"
        r"(?:sembuh|dinyatakan sembuh)\b",
        flags=re.IGNORECASE,
    ),
]


HOSPITALIZED_PATTERNS = [
    re.compile(
        r"\b(?P<count>\d[\d.]*)\s+"
        r"(?:orang|pasien|penderita)?\s*"
        r"(?:masih\s+|sedang\s+)?"
        r"(?:dirawat|menjalani perawatan)\b",
        flags=re.IGNORECASE,
    ),
]

TREND_PATTERNS = {
    ArticleFact.Trend.INCREASING: [
        r"\bmeningkat\b",
        r"\bpeningkatan\b",
        r"\bmelonjak\b",
        r"\bbertambah\b",
        r"\bnaik\b",
        r"\bkenaikan\b",
    ],
    ArticleFact.Trend.DECREASING: [
        r"\bmenurun\b",
        r"\bpenurunan\b",
        r"\bberkurang\b",
        r"\bturun\b",
    ],
    ArticleFact.Trend.STABLE: [
        r"\bstabil\b",
        r"\btetap\b",
        r"\btidak mengalami perubahan\b",
    ],
    ArticleFact.Trend.NEW_OCCURRENCE: [
        r"\bkasus pertama\b",
        r"\bpertama kali ditemukan\b",
        r"\btemuan baru\b",
        r"\bkasus baru ditemukan\b",
    ],
    ArticleFact.Trend.SPREADING: [
        r"\bmeluas\b",
        r"\bmenyebar\b",
        r"\bpenyebaran\b",
        r"\bwilayah baru\b",
    ],
}


# Kata seperti "peningkatan" hanya boleh menjadi tren jika konteks di
# kalimat yang sama membicarakan keadaan epidemiologis. Tanpa pembatas ini,
# frasa "peningkatan kapasitas teknis" atau "peningkatan kewaspadaan" akan
# keliru dibaca sebagai peningkatan kasus.
EPIDEMIOLOGICAL_TREND_CONTEXT = re.compile(
    r"\b(?:jumlah\s+kasus|angka\s+kasus|kasus|kematian|pasien|penderita|"
    r"korban|rawat\s+inap|kejadian|wabah|penularan)\b",
    flags=re.IGNORECASE,
)

NON_EPIDEMIOLOGICAL_TREND_OBJECT = re.compile(
    r"\b(?:peningkatan|kenaikan|penurunan)\s+(?:kapasitas|kewaspadaan|"
    r"surveilans|pengawasan|layanan|pelayanan|upaya|respons|respon|"
    r"koordinasi|kolaborasi|jejaring|target|cakupan|vaksinasi|imunisasi|"
    r"pemeriksaan|deteksi|pelacakan|anggaran|fasilitas|tenaga|"
    r"sumber\s+daya)\b",
    flags=re.IGNORECASE,
)


NEGATION_PATTERNS = [
    r"\btidak ada\b",
    r"\btidak ditemukan\b",
    r"\bbukan\b",
    r"\bbelum ada\b",
    r"\bnihil\b",
]


@dataclass(frozen=True)
class NumericMention:
    value: int
    matched_text: str
    start: int
    end: int


@dataclass(frozen=True)
class TrendMention:
    trend: str
    matched_text: str
    start: int
    end: int


@dataclass(frozen=True)
class ExtractedFactCandidate:
    disease: Disease | None
    location: Location | None
    event_date: date | None
    case_count: int | None
    death_count: int | None
    recovery_count: int | None
    hospitalized_count: int | None
    trend: str
    evidence_text: str
    confidence_score: float


@dataclass
class FactExtractionResult:
    article: Article
    candidates: list[ExtractedFactCandidate] = field(
        default_factory=list
    )
    facts_created: int = 0
    facts_skipped: int = 0


def normalize_number(raw_value: str) -> int | None:
    value = raw_value.strip().lower()

    if value in NUMBER_WORDS:
        return NUMBER_WORDS[value]

    cleaned = re.sub(r"[^\d]", "", value)

    if not cleaned:
        return None

    try:
        return int(cleaned)
    except ValueError:
        return None


def find_first_numeric_mention(
    *,
    text: str,
    patterns: list[re.Pattern],
) -> NumericMention | None:
    for pattern in patterns:
        match = pattern.search(text)

        if not match:
            continue

        value = normalize_number(
            match.group("count")
        )

        if value is None:
            continue

        return NumericMention(
            value=value,
            matched_text=match.group(0),
            start=match.start(),
            end=match.end(),
        )

    return None


def has_negation_near_match(
    *,
    text: str,
    start: int,
    window: int = 40,
) -> bool:
    context_start = max(0, start - window)
    context = text[context_start:start].lower()

    return any(
        re.search(pattern, context)
        for pattern in NEGATION_PATTERNS
    )


def detect_trend(
    text: str,
) -> TrendMention | None:
    candidates = []

    for trend, expressions in TREND_PATTERNS.items():
        for expression in expressions:
            for match in re.finditer(
                expression,
                text,
                flags=re.IGNORECASE,
            ):
                if has_negation_near_match(
                    text=text,
                    start=match.start(),
                ):
                    continue

                sentence_start = max(
                    text.rfind(".", 0, match.start()),
                    text.rfind("!", 0, match.start()),
                    text.rfind("?", 0, match.start()),
                    text.rfind("\n", 0, match.start()),
                ) + 1
                sentence_end_candidates = [
                    position
                    for position in (
                        text.find(".", match.end()),
                        text.find("!", match.end()),
                        text.find("?", match.end()),
                        text.find("\n", match.end()),
                    )
                    if position >= 0
                ]
                sentence_end = (
                    min(sentence_end_candidates)
                    if sentence_end_candidates
                    else len(text)
                )
                sentence = text[sentence_start:sentence_end]
                local_start = match.start() - sentence_start

                false_object_match = (
                    NON_EPIDEMIOLOGICAL_TREND_OBJECT.search(sentence)
                )
                if (
                    false_object_match
                    and false_object_match.start()
                    <= local_start
                    < false_object_match.end()
                ):
                    continue

                context_start = max(0, local_start - 80)
                context_end = min(
                    len(sentence),
                    local_start + len(match.group(0)) + 80,
                )
                nearby_context = sentence[context_start:context_end]
                if not EPIDEMIOLOGICAL_TREND_CONTEXT.search(
                    nearby_context
                ):
                    continue

                candidates.append(
                    TrendMention(
                        trend=trend,
                        matched_text=match.group(0),
                        start=match.start(),
                        end=match.end(),
                    )
                )

    if candidates:
        return min(candidates, key=lambda candidate: candidate.start)

    return None


def parse_indonesian_date(
    text: str,
    *,
    reference_date: date,
) -> date | None:
    absolute_pattern = re.compile(
        r"\b(?P<day>\d{1,2})\s+"
        r"(?P<month>"
        + "|".join(MONTHS_ID.keys())
        + r")\s+"
        r"(?P<year>\d{4})\b",
        flags=re.IGNORECASE,
    )

    match = absolute_pattern.search(text)

    if match:
        try:
            return date(
                int(match.group("year")),
                MONTHS_ID[
                    match.group("month").lower()
                ],
                int(match.group("day")),
            )
        except ValueError:
            return None

    text_lower = text.lower()

    if re.search(r"\bhari ini\b", text_lower):
        return reference_date

    if re.search(r"\bkemarin\b", text_lower):
        return reference_date - timedelta(days=1)

    return None


def extract_evidence_sentence(
    *,
    text: str,
    anchor_start: int,
    max_length: int = 700,
) -> str:
    sentence_start = text.rfind(
        ".",
        0,
        anchor_start,
    )

    sentence_start = (
        0
        if sentence_start == -1
        else sentence_start + 1
    )

    sentence_end = text.find(
        ".",
        anchor_start,
    )

    sentence_end = (
        len(text)
        if sentence_end == -1
        else sentence_end + 1
    )

    sentence = text[
        sentence_start:sentence_end
    ].strip()

    return sentence[:max_length]


def get_primary_disease(
    article: Article,
) -> Disease | None:
    relation = (
        ArticleDisease.objects.filter(
            article=article,
        )
        .select_related("disease")
        .order_by(
            "-is_primary",
            "-confidence_score",
            "created_at",
        )
        .first()
    )

    return relation.disease if relation else None


def get_primary_location(
    article: Article,
) -> Location | None:
    relation = (
        ArticleLocation.objects.filter(
            article=article,
        )
        .select_related("location")
        .order_by(
            "-is_primary",
            "-confidence_score",
            "created_at",
        )
        .first()
    )

    return relation.location if relation else None


def calculate_fact_confidence(
    *,
    disease: Disease | None,
    location: Location | None,
    has_case_count: bool,
    has_death_count: bool,
    has_event_date: bool,
    has_trend: bool,
) -> float:
    score = 0.30

    if disease:
        score += 0.15

    if location:
        score += 0.15

    if has_case_count:
        score += 0.15

    if has_death_count:
        score += 0.10

    if has_event_date:
        score += 0.05

    if has_trend:
        score += 0.10

    return min(score, 1.0)


def build_fact_candidate(
    article: Article,
) -> ExtractedFactCandidate | None:
    text = (
        f"{article.title}. "
        f"{article.content_text}"
    )

    case_mention = find_first_numeric_mention(
        text=text,
        patterns=CASE_PATTERNS,
    )

    death_mention = find_first_numeric_mention(
        text=text,
        patterns=DEATH_PATTERNS,
    )

    recovery_mention = find_first_numeric_mention(
        text=text,
        patterns=RECOVERY_PATTERNS,
    )

    hospitalized_mention = find_first_numeric_mention(
        text=text,
        patterns=HOSPITALIZED_PATTERNS,
    )

    trend_mention = detect_trend(text)

    reference_date = (
        article.published_at.date()
        if article.published_at
        else timezone.localdate()
    )

    event_date = parse_indonesian_date(
        text,
        reference_date=reference_date,
    )

    disease = get_primary_disease(article)
    location = get_primary_location(article)

    if not any(
        [
            disease,
            location,
            case_mention,
            death_mention,
            recovery_mention,
            hospitalized_mention,
            trend_mention,
            event_date,
        ]
    ):
        return None

    anchors = [
        mention.start
        for mention in [
            case_mention,
            death_mention,
            recovery_mention,
            hospitalized_mention,
            trend_mention,
        ]
        if mention is not None
    ]

    anchor_start = min(anchors) if anchors else 0

    evidence_text = extract_evidence_sentence(
        text=text,
        anchor_start=anchor_start,
    )

    confidence = calculate_fact_confidence(
        disease=disease,
        location=location,
        has_case_count=case_mention is not None,
        has_death_count=death_mention is not None,
        has_event_date=event_date is not None,
        has_trend=trend_mention is not None,
    )

    return ExtractedFactCandidate(
        disease=disease,
        location=location,
        event_date=event_date,
        case_count=(
            case_mention.value
            if case_mention
            else None
        ),
        death_count=(
            death_mention.value
            if death_mention
            else None
        ),
        recovery_count=(
            recovery_mention.value
            if recovery_mention
            else None
        ),
        hospitalized_count=(
            hospitalized_mention.value
            if hospitalized_mention
            else None
        ),
        trend=(
            trend_mention.trend
            if trend_mention
            else ArticleFact.Trend.UNKNOWN
        ),
        evidence_text=evidence_text,
        confidence_score=confidence,
    )


@transaction.atomic
def extract_article_facts(
    article: Article,
) -> FactExtractionResult:
    result = FactExtractionResult(
        article=article,
    )

    candidate = build_fact_candidate(article)

    if candidate is None:
        result.facts_skipped += 1
        return result

    result.candidates.append(candidate)

    existing_fact = ArticleFact.objects.filter(
        article=article,
        disease=candidate.disease,
        location=candidate.location,
        event_date=candidate.event_date,
        case_count=candidate.case_count,
        death_count=candidate.death_count,
        recovery_count=candidate.recovery_count,
        hospitalized_count=candidate.hospitalized_count,
        trend=candidate.trend,
    ).first()

    if existing_fact:
        result.facts_skipped += 1
        return result

    ArticleFact.objects.create(
        article=article,
        disease=candidate.disease,
        location=candidate.location,
        event_date=candidate.event_date,
        case_count=candidate.case_count,
        death_count=candidate.death_count,
        recovery_count=candidate.recovery_count,
        hospitalized_count=(
            candidate.hospitalized_count
        ),
        trend=candidate.trend,
        fact_text=candidate.evidence_text,
        confidence_score=(
            candidate.confidence_score
        ),
        extraction_method=(
            ExtractionMethod.RULE_BASED
        ),
        validation_status=(
            ValidationStatus.UNREVIEWED
        ),
    )

    result.facts_created += 1

    return result
