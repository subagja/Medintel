import hashlib
import re
from django.db.models import Q
from intel.models import Signal


def normalize_dedup_text(value):
    if not value:
        return ""

    value = str(value).lower().strip()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def build_signal_fingerprint(
    title="",
    disease_tag="",
    raw_location_text="",
    source_name="",
):
    """
    Fingerprint ringan untuk membantu mendeteksi artikel/signal yang mirip.
    Tidak wajib disimpan ke DB; dipakai runtime untuk guard noise.
    """
    normalized = " | ".join([
        normalize_dedup_text(title),
        normalize_dedup_text(disease_tag),
        normalize_dedup_text(raw_location_text),
        normalize_dedup_text(source_name),
    ])

    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def find_existing_signal(
    source_url=None,
    resolved_url=None,
    title=None,
    disease_tag=None,
    raw_location_text=None,
    source_name=None,
):
    """
    Cari signal lama berdasarkan:
    1. source_url / resolved_url
    2. exact title
    3. title + disease + location sederhana
    """

    source_url = (source_url or "").strip()
    resolved_url = (resolved_url or "").strip()
    title = (title or "").strip()
    disease_tag = (disease_tag or "").strip()
    raw_location_text = (raw_location_text or "").strip()

    url_q = Q()

    if source_url:
        url_q |= Q(source_url=source_url) | Q(resolved_url=source_url)

    if resolved_url:
        url_q |= Q(source_url=resolved_url) | Q(resolved_url=resolved_url)

    if url_q:
        existing = Signal.objects.filter(url_q).order_by("-updated_at", "-created_at").first()
        if existing:
            return existing

    if title:
        existing = Signal.objects.filter(title__iexact=title).order_by("-updated_at", "-created_at").first()
        if existing:
            return existing

    if title and disease_tag and raw_location_text:
        title_norm = normalize_dedup_text(title)
        title_part = title_norm[:80]

        if title_part:
            existing = (
                Signal.objects.filter(
                    title__icontains=title_part,
                    disease_tag__iexact=disease_tag,
                    raw_location_text__iexact=raw_location_text,
                )
                .order_by("-updated_at", "-created_at")
                .first()
            )
            if existing:
                return existing

    return None


def should_skip_as_noise(
    source_url=None,
    resolved_url=None,
    title=None,
    disease_tag=None,
    raw_location_text=None,
    source_name=None,
):
    """
    Return:
    - True, existing_signal  → signal lama sudah noise, jangan ingest ulang.
    - False, existing_signal → signal lama ada tapi bukan noise.
    - False, None            → belum pernah ada.
    """

    existing = find_existing_signal(
        source_url=source_url,
        resolved_url=resolved_url,
        title=title,
        disease_tag=disease_tag,
        raw_location_text=raw_location_text,
        source_name=source_name,
    )

    if existing and existing.status == "noise":
        return True, existing

    return False, existing