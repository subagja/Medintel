from __future__ import annotations

import re
from dataclasses import dataclass

from apps.entities.surveillance import get_active_surveillance_diseases


@dataclass(frozen=True)
class GoogleNewsQueryBatch:
    """Satu kelompok istilah Disease Master untuk satu permintaan feed."""

    index: int
    total: int
    query: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class GoogleNewsDiseaseScope:
    """Ringkasan cakupan otomatis yang ditampilkan dan dipakai crawler."""

    disease_count: int
    term_count: int
    batches: tuple[GoogleNewsQueryBatch, ...]


def _normalized_key(term: str) -> str:
    return re.sub(r"\s+", " ", term).strip().casefold()


def _is_searchable_term(term: str) -> bool:
    """Tolak alias yang terlalu pendek dan rawan menjadi kata umum.

    Nama utama penyakit dan canonical name umumnya panjang. Penyaringan ini
    terutama mencegah alias dua karakter seperti ``AI`` membanjiri hasil.
    Akronim penting tiga karakter atau lebih, misalnya DBD/HIV/AFP, tetap
    digunakan.
    """

    compact = re.sub(r"[^\w]+", "", term, flags=re.UNICODE)
    return len(compact) >= 3


def _query_token(term: str) -> str:
    escaped = term.replace('"', "").strip()
    if re.fullmatch(r"[\w]+", escaped, flags=re.UNICODE):
        return escaped
    return f'"{escaped}"'


def build_google_news_disease_scope(
    *,
    max_query_chars: int = 420,
) -> GoogleNewsDiseaseScope:
    """Bangun batch pencarian dari master surveilans aktif.

    Sumber istilah tunggalnya adalah Disease aktif yang terhubung dengan
    SurveillanceProgram aktif melalui SurveillanceDisease aktif. Setiap
    perubahan nama, canonical name, alias aktif, atau keanggotaan program akan
    otomatis tercermin pada proses Google News berikutnya.
    """

    if max_query_chars < 50:
        raise ValueError("Batas karakter kueri minimal 50.")

    disease_count = 0
    seen_terms: set[str] = set()
    ordered_terms: list[str] = []

    for disease in get_active_surveillance_diseases():
        disease_terms = [disease.name]
        if disease.canonical_name:
            disease_terms.append(disease.canonical_name)
        disease_terms.extend(
            alias.alias
            for alias in disease.aliases.all()
            if alias.is_active
        )

        accepted_for_disease = False
        for raw_term in disease_terms:
            term = re.sub(r"\s+", " ", (raw_term or "")).strip()
            key = _normalized_key(term)
            if not key or key in seen_terms or not _is_searchable_term(term):
                continue
            seen_terms.add(key)
            ordered_terms.append(term)
            accepted_for_disease = True

        if accepted_for_disease:
            disease_count += 1

    raw_batches: list[tuple[str, ...]] = []
    current_terms: list[str] = []
    current_length = 0

    for term in ordered_terms:
        token = _query_token(term)
        added_length = len(token) + (4 if current_terms else 0)
        if current_terms and current_length + added_length > max_query_chars:
            raw_batches.append(tuple(current_terms))
            current_terms = []
            current_length = 0

        current_terms.append(term)
        current_length += len(token) + (4 if len(current_terms) > 1 else 0)

    if current_terms:
        raw_batches.append(tuple(current_terms))

    total = len(raw_batches)
    batches = tuple(
        GoogleNewsQueryBatch(
            index=index,
            total=total,
            query=" OR ".join(_query_token(term) for term in terms),
            terms=terms,
        )
        for index, terms in enumerate(raw_batches, start=1)
    )

    return GoogleNewsDiseaseScope(
        disease_count=disease_count,
        term_count=len(ordered_terms),
        batches=batches,
    )
