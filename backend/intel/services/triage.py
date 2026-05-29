# backend/intel/services/triage.py

from django.utils import timezone


HIGH_RISK_KEYWORDS = [
    "klb",
    "kejadian luar biasa",
    "wabah",
    "meninggal",
    "kematian",
    "meningkat",
    "lonjakan",
    "merebak",
    "terjangkit",
    "kasus bertambah",
    "dirawat",
    "isolasi",
    "suspek",
    "positif",
]

LOW_VALUE_KEYWORDS = [
    "sosialisasi",
    "edukasi",
    "penyuluhan",
    "seminar",
    "pelatihan",
    "imbauan",
    "kampanye",
    "peringatan hari",
    "lomba",
    "donor darah",
]


def _safe_text(value):
    return (value or "").lower().strip()


def _get_risk_score(signal):
    """
    Menyesuaikan dengan field existing kamu.
    Di template kamu terlihat memakai threat_score.
    Kalau project lama masih punya score, fallback tetap disediakan.
    """
    return (
        getattr(signal, "threat_score", None)
        or getattr(signal, "score", None)
        or 0
    )


def _has_primary_location(signal):
    """
    Aman untuk related_name='locations'.
    Kalau signal.locations belum tersedia, fallback ke False.
    """
    try:
        return signal.locations.filter(location__isnull=False).exists()
    except Exception:
        return False


def _get_geocode_status(signal):
    return (
        getattr(signal, "geocode_status", None)
        or getattr(signal, "mapping_status", None)
        or ""
    )


def calculate_confidence_score(signal):
    """
    Confidence score = keyakinan kualitas data.
    Tidak sama dengan risk score.
    Tidak mengubah status signal.
    """
    score = 0

    if getattr(signal, "disease_tag", None):
        score += 20

    if getattr(signal, "published_at", None):
        score += 10

    if getattr(signal, "source_url", None):
        score += 10

    if getattr(signal, "source", None):
        score += 5

    geocode_status = _get_geocode_status(signal)
    if geocode_status in ["matched", "ok", "success", "resolved"]:
        score += 20

    if _has_primary_location(signal):
        score += 25

    if getattr(signal, "content", None):
        score += 5

    assessment_status = getattr(signal, "assessment_status", None)
    if assessment_status in ["success", "completed", "ok"]:
        score += 5

    return min(score, 100)


def classify_signal_triage(signal):
    """
    Menghasilkan:
    - triage_priority
    - approval_recommendation
    - confidence_score
    - triage_reason

    Tidak mengubah signal.status.
    """
    title = _safe_text(getattr(signal, "title", ""))
    content = _safe_text(getattr(signal, "content", ""))
    combined_text = f"{title} {content}"

    risk_score = _get_risk_score(signal)
    confidence = calculate_confidence_score(signal)
    geocode_status = _get_geocode_status(signal)

    has_disease = bool(getattr(signal, "disease_tag", None))
    has_location = _has_primary_location(signal)

    high_keyword_hits = [
        kw for kw in HIGH_RISK_KEYWORDS if kw in combined_text
    ]

    low_value_hits = [
        kw for kw in LOW_VALUE_KEYWORDS if kw in combined_text
    ]

    reasons = []

    if has_disease:
        reasons.append(f"Penyakit terdeteksi: {signal.disease_tag}")
    else:
        reasons.append("Penyakit belum terdeteksi jelas")

    if has_location:
        reasons.append("Lokasi berhasil dipetakan")
    else:
        reasons.append("Lokasi belum berhasil dipetakan")

    reasons.append(f"Risk score: {risk_score}")
    reasons.append(f"Confidence score: {confidence}")

    if high_keyword_hits:
        reasons.append(
            "Indikator eskalasi: " + ", ".join(high_keyword_hits[:5])
        )

    if low_value_hits:
        reasons.append(
            "Indikator konten bernilai rendah/noise: "
            + ", ".join(low_value_hits[:5])
        )

    # Rule utama
    if low_value_hits and risk_score < 45:
        priority = "noise_candidate"
        recommendation = "noise"
        reasons.append("Direkomendasikan sebagai kandidat noise")

    elif risk_score >= 75 and confidence >= 70 and has_disease and has_location:
        priority = "urgent"
        recommendation = "approve"
        reasons.append("Skor risiko dan confidence tinggi; siap direview untuk approval")

    elif risk_score >= 60 and confidence >= 50:
        priority = "high"
        recommendation = "review"
        reasons.append("Skor cukup tinggi; perlu review analis")

    elif risk_score >= 50 and not has_location:
        priority = "medium"
        recommendation = "fix_location"
        reasons.append("Sinyal cukup penting tetapi lokasi perlu diperbaiki")

    elif risk_score < 30 and confidence < 40:
        priority = "noise_candidate"
        recommendation = "noise"
        reasons.append("Skor dan confidence rendah")

    elif confidence < 45:
        priority = "low"
        recommendation = "review"
        reasons.append("Confidence rendah; perlu review manual")

    else:
        priority = "medium"
        recommendation = "review"
        reasons.append("Perlu review manual")

    return {
        "confidence_score": confidence,
        "triage_priority": priority,
        "approval_recommendation": recommendation,
        "triage_reason": "\n".join(reasons),
    }


def apply_triage(signal, save=True):
    """
    Apply triage ke satu signal.
    Aman: tidak mengubah status.
    """
    result = classify_signal_triage(signal)

    signal.confidence_score = result["confidence_score"]
    signal.triage_priority = result["triage_priority"]
    signal.approval_recommendation = result["approval_recommendation"]
    signal.triage_reason = result["triage_reason"]
    signal.triaged_at = timezone.now()

    if save:
        signal.save(
            update_fields=[
                "confidence_score",
                "triage_priority",
                "approval_recommendation",
                "triage_reason",
                "triaged_at",
            ]
        )

    return signal