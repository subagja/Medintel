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
    try:
        assessment = article.validation_assessment
    except (AttributeError, ObjectDoesNotExist):
        return "uncertain"
    text = assessment.assessment_notes or ""
    match = _TYPE_PATTERN.search(text)
    value = match.group(1).lower() if match else "uncertain"
    return value if value in KNOWN_TYPES else "uncertain"


def contextual_review_info(article):
    """Status untuk tahap tinjau konteks, BUKAN status validasi final.

    Minimal ada penyakit dan tepat satu lokasi utama yang tidak ditolak.
    Tidak mewajibkan jumlah kasus pada berita risiko/pencegahan, namun
    tetap membutuhkan keputusan analis sebelum menjadi data pendukung.
    """
    category = information_type_from_article(article)
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
    return {"information_type": category, "context_review_ready": ready,
            "contextual": contextual, "context_review_label": label}
