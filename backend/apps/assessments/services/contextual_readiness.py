"""Kesiapan tinjau konteks OSINT (terpisah dari kelengkapan bukti kasus).

Kategori diambil dari keluaran AI Patch 02A yang sudah tercatat pada penilaian.
Tidak membuat data kasus, tidak mengubah keputusan validasi, dan tidak
menganggap artikel konteks sebagai bukti kejadian penyakit.
"""

import re
from django.core.exceptions import ObjectDoesNotExist

CONTEXT_TYPES = frozenset({"risk_indicator", "prevention_response"})
KNOWN_TYPES = frozenset({"case_event", "risk_indicator", "prevention_response", "other", "uncertain"})
_TYPE_PATTERN = re.compile(r"\[Jenis informasi:\s*([a-z_]+)\]", re.IGNORECASE)


def information_type_from_article(article):
    """Baca kategori AI yang terdokumentasi; tidak mengklasifikasi secara heuristik."""
    from .information_type_review import confirmed_information_type
    manual = confirmed_information_type(article)
    if manual is not None:
        return manual
    try:
        assessment = article.validation_assessment
    except (AttributeError, ObjectDoesNotExist):
        return "uncertain"
    text = assessment.assessment_notes or ""
    match = _TYPE_PATTERN.search(text)
    value = match.group(1).lower() if match else "uncertain"
    return value if value in KNOWN_TYPES else "uncertain"


# MEDINTEL-02B3-CONTEXT-CANDIDATES
# Petunjuk judul untuk triage, bukan keputusan validasi final.
_PREVENTION = re.compile(r"\b(?:vaksinasi|imunisasi|cakupan vaksin|kampanye imunisasi|program vaksin)\b", re.I)
_RISK = re.compile(r"\b(?:penurunan cakupan imunisasi|cakupan imunisasi rendah|cakupan vaksinasi rendah|risiko penularan|faktor risiko|peningkatan populasi nyamuk)\b", re.I)
_CASE_EVENT = re.compile(r"\b(?:kasus|pasien|penderita|kematian|meninggal|wabah|kejadian luar biasa|klb)\b", re.I)


def context_candidate_from_title(article):
    """Kandidat konteks dari judul; bukan kategori hasil AI."""
    title = (getattr(article, "title", None) or "").strip()
    if not title or _CASE_EVENT.search(title):
        return None
    if _RISK.search(title):
        return "risk_indicator"
    if _PREVENTION.search(title):
        return "prevention_response"
    return None


def contextual_review_info(article):
    """Status untuk tahap tinjau konteks, BUKAN status validasi final.

    Minimal ada penyakit dan tepat satu lokasi utama yang tidak ditolak.
    Tidak mewajibkan jumlah kasus pada berita risiko/pencegahan, namun
    tetap membutuhkan keputusan analis sebelum menjadi data pendukung.
    """
    category = information_type_from_article(article)
    candidate = context_candidate_from_title(article) if category == "uncertain" else None
    contextual = category in CONTEXT_TYPES
    has_disease = bool(article.extraction_has_disease)
    has_single_location = bool(article.extraction_has_location and article.extraction_primary_count == 1)
    ready = contextual and has_disease and has_single_location
    if not contextual:
        label = "Bukti kasus / kategori belum terkonfirmasi"
    elif ready:
        label = "Konteks relevan — siap ditinjau analis (bukan bukti kasus)"
    else:
        label = "Konteks relevan — penyakit/lokasi perlu dilengkapi"
    review = (article.raw_metadata or {}).get("information_type_review") or {}
    verified = isinstance(review, dict) and review.get("category") in KNOWN_TYPES
    return {"information_type": category, "context_review_ready": ready,
            "contextual": contextual, "context_review_label": label,
            "classification_verified": verified, "classification_reviewer": review.get("reviewed_by", "") if verified else "",
            "context_candidate_type": candidate,
            "context_candidate": bool(candidate),
            "context_candidate_ready": bool(candidate and has_disease and has_single_location)}
