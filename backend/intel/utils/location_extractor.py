import re
from dataclasses import dataclass
from typing import Optional

from intel.models import Location, LocationAlias


@dataclass
class ExtractedLocationResult:
    raw_location_text: str = ""
    method: str = ""
    confidence: float = 0.0
    note: str = ""


def normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("&", " dan ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def build_search_text(title: str, content: str) -> str:
    text = " ".join([
        (title or "").strip(),
        (content or "").strip(),
    ])
    return normalize_text(text)


def candidate_patterns(name: str) -> list[str]:
    """
    Hasilkan variasi pola pencarian berbasis kata utuh.
    """
    base = normalize_text(name)
    if not base:
        return []

    escaped = re.escape(base)
    patterns = [rf"\b{escaped}\b"]

    # variasi kabupaten / kota
    patterns.append(rf"\bkab(?:upaten|\.)?\s+{escaped}\b")
    patterns.append(rf"\bkota\s+{escaped}\b")

    return patterns


def choose_better_match(current, challenger):
    """
    current/challenger = tuple(score, text, method, confidence, note)
    Pilih yang lebih spesifik:
    1. skor lebih tinggi
    2. teks lebih panjang
    """
    if current is None:
        return challenger
    if challenger[0] > current[0]:
        return challenger
    if challenger[0] == current[0] and len(challenger[1]) > len(current[1]):
        return challenger
    return current


def extract_location_from_text(title: str, content: str) -> ExtractedLocationResult:
    text = build_search_text(title, content)
    if not text:
        return ExtractedLocationResult(
            raw_location_text="",
            method="empty_text",
            confidence=0.0,
            note="title and content empty",
        )

    best = None

    # 1. Alias dulu, karena biasanya lebih kaya variasi
    aliases = (
        LocationAlias.objects.select_related("location")
        .filter(
            is_active=True,
            location__is_active=True,
            location__is_false_positive=False,
        )
        .only("alias", "normalized_alias", "location__display_name", "location__level")
    )

    for alias in aliases:
        alias_text = alias.alias or alias.normalized_alias or ""
        if not alias_text:
            continue

        for pattern in candidate_patterns(alias_text):
            if re.search(pattern, text, flags=re.IGNORECASE):
                loc = alias.location
                display = loc.display_name or loc.name

                score = 50
                if loc.level in {"city", "regency"}:
                    score += 20
                if len(alias_text) >= 8:
                    score += 5

                best = choose_better_match(
                    best,
                    (
                        score,
                        display,
                        "alias_match",
                        0.85,
                        f"matched alias: {alias.alias}",
                    ),
                )
                break

    # 2. Direct Location match
    locations = (
        Location.objects.filter(is_active=True, is_false_positive=False)
        .only("name", "display_name", "normalized_name", "level")
    )

    for loc in locations:
        names_to_try = [
            loc.display_name or "",
            loc.name or "",
        ]
        for name in names_to_try:
            if not name:
                continue
            for pattern in candidate_patterns(name):
                if re.search(pattern, text, flags=re.IGNORECASE):
                    display = loc.display_name or loc.name
                    score = 40
                    if loc.level in {"city", "regency"}:
                        score += 20
                    if len(display) >= 8:
                        score += 5

                    best = choose_better_match(
                        best,
                        (
                            score,
                            display,
                            "location_match",
                            0.80,
                            f"matched location: {display}",
                        ),
                    )
                    break

    if best:
        return ExtractedLocationResult(
            raw_location_text=best[1],
            method=best[2],
            confidence=best[3],
            note=best[4],
        )

    return ExtractedLocationResult(
        raw_location_text="",
        method="not_found",
        confidence=0.0,
        note="no location candidate found in title/content",
    )