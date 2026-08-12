"""Kendali analis atas korelasi sinyal otomatis.

Sistem otomatis membentuk/menggabungkan sinyal berdasarkan penyakit +
lokasi yang sama dalam rentang waktu tertentu (lihat
``services/generation.py``). Modul ini menyediakan kendali MANUAL untuk
analis mengoreksi hasil otomatis itu -- menggabungkan dua sinyal yang
seharusnya satu kejadian, memisahkan sinyal yang keliru digabung,
menambah/melepas artikel bukti, dan menandai jenis dukungan artikel
(penguat/bertentangan/dsb).
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.articles.models import Article

from ..models import Signal, SignalArticle, SignalHistory
from .lifecycle import record_signal_history, validate_active_user


@transaction.atomic
def attach_article_to_signal(
    *,
    signal: Signal,
    article: Article,
    actor,
    support_type: str = SignalArticle.SupportType.SUPPORTING,
    notes: str = "",
) -> SignalArticle:
    """Tambahkan artikel sebagai bukti tambahan ke sinyal, manual."""
    validate_active_user(actor, "Analis")

    if signal.status in {Signal.Status.REJECTED, Signal.Status.CLOSED}:
        raise ValidationError(
            "Tidak bisa menambah artikel pada sinyal yang sudah "
            "ditolak/ditutup."
        )

    link, created = SignalArticle.objects.get_or_create(
        signal=signal,
        article=article,
        defaults={
            "support_type": support_type,
            "added_by": actor,
        },
    )

    if not created:
        raise ValidationError(
            "Artikel ini sudah terhubung dengan sinyal ini."
        )

    record_signal_history(
        signal=signal,
        from_status=signal.status,
        to_status=signal.status,
        changed_by=actor,
        reason=(
            notes.strip()
            or f'Artikel "{article.title}" ditambahkan manual sebagai bukti.'
        ),
        metadata={
            "action": "article_attached",
            "article_id": str(article.id),
            "support_type": support_type,
        },
    )

    return link


@transaction.atomic
def detach_article_from_signal(
    *,
    signal_article: SignalArticle,
    actor,
    reason: str,
) -> None:
    """Lepaskan artikel dari sinyal (bukan hapus artikelnya, cuma
    keterkaitannya). Sinyal wajib tetap punya minimal satu artikel
    bukti setelahnya -- kalau tidak, gunakan reject/close sinyal.
    """
    validate_active_user(actor, "Analis")

    if not reason.strip():
        raise ValidationError(
            "Alasan pelepasan artikel wajib diisi untuk jejak audit."
        )

    signal = signal_article.signal
    remaining = signal.signal_articles.exclude(
        id=signal_article.id
    ).count()

    if remaining == 0:
        raise ValidationError(
            "Tidak bisa melepas artikel terakhir dari sinyal. "
            "Gunakan tolak/tutup sinyal kalau sinyal ini memang "
            "tidak valid."
        )

    article_title = signal_article.article.title
    signal_article.delete()

    record_signal_history(
        signal=signal,
        from_status=signal.status,
        to_status=signal.status,
        changed_by=actor,
        reason=reason.strip(),
        metadata={
            "action": "article_detached",
            "article_title": article_title,
        },
    )


@transaction.atomic
def update_article_support_type(
    *,
    signal_article: SignalArticle,
    support_type: str,
    actor,
) -> SignalArticle:
    """Ubah jenis dukungan artikel (penguat/bertentangan/dsb)."""
    validate_active_user(actor, "Analis")

    valid_types = {
        choice for choice, _label in SignalArticle.SupportType.choices
    }
    if support_type not in valid_types:
        raise ValidationError("Jenis dukungan tidak dikenali.")

    previous = signal_article.support_type
    if previous == support_type:
        return signal_article

    signal_article.support_type = support_type
    signal_article.save(update_fields=["support_type"])

    record_signal_history(
        signal=signal_article.signal,
        from_status=signal_article.signal.status,
        to_status=signal_article.signal.status,
        changed_by=actor,
        reason=(
            f'Jenis dukungan artikel "{signal_article.article.title}" '
            f"diubah dari {previous} menjadi {support_type}."
        ),
        metadata={
            "action": "support_type_changed",
            "article_id": str(signal_article.article_id),
            "from_support_type": previous,
            "to_support_type": support_type,
        },
    )

    return signal_article


@transaction.atomic
def merge_signals(
    *,
    primary_signal: Signal,
    secondary_signal: Signal,
    actor,
    reason: str,
) -> Signal:
    """Gabungkan dua sinyal yang analis yakini mewakili kejadian yang
    sama (mis. beda sedikit lokasi/rentang tanggal sehingga tidak
    ke-gabung otomatis). Semua artikel bukti `secondary_signal`
    dipindah ke `primary_signal`, lalu `secondary_signal` ditutup
    dengan catatan digantikan.
    """
    validate_active_user(actor, "Analis")

    if primary_signal.id == secondary_signal.id:
        raise ValidationError(
            "Tidak bisa menggabungkan sinyal dengan dirinya sendiri."
        )

    if not reason.strip():
        raise ValidationError(
            "Alasan penggabungan wajib diisi untuk jejak audit."
        )

    closed_statuses = {Signal.Status.REJECTED, Signal.Status.CLOSED}
    if (
        primary_signal.status in closed_statuses
        or secondary_signal.status in closed_statuses
    ):
        raise ValidationError(
            "Sinyal yang sudah ditolak/ditutup tidak bisa "
            "digabungkan."
        )

    existing_article_ids = set(
        primary_signal.signal_articles.values_list(
            "article_id", flat=True
        )
    )

    moved = 0
    skipped = 0
    for link in secondary_signal.signal_articles.all():
        if link.article_id in existing_article_ids:
            # Artikel sudah ada di sinyal utama -- hindari duplikat
            # constraint, cukup buang keterkaitan lama.
            link.delete()
            skipped += 1
            continue
        link.signal = primary_signal
        link.save(update_fields=["signal"])
        moved += 1

    secondary_code = secondary_signal.code
    secondary_signal.status = Signal.Status.CLOSED
    secondary_signal.save(update_fields=["status"])

    record_signal_history(
        signal=secondary_signal,
        from_status=secondary_signal.status,
        to_status=Signal.Status.CLOSED,
        changed_by=actor,
        reason=(
            f"Digabungkan ke sinyal {primary_signal.code}. {reason.strip()}"
        ),
        metadata={
            "action": "merged_into",
            "target_signal_code": primary_signal.code,
            "articles_moved": moved,
            "articles_deduplicated": skipped,
        },
    )

    record_signal_history(
        signal=primary_signal,
        from_status=primary_signal.status,
        to_status=primary_signal.status,
        changed_by=actor,
        reason=(
            f"Menerima gabungan dari sinyal {secondary_code}. "
            f"{reason.strip()}"
        ),
        metadata={
            "action": "merged_from",
            "source_signal_code": secondary_code,
            "articles_moved": moved,
            "articles_deduplicated": skipped,
        },
    )

    return primary_signal


@transaction.atomic
def split_signal(
    *,
    source_signal: Signal,
    article_ids: list[str],
    actor,
    new_title: str,
    new_summary: str,
    reason: str,
) -> Signal:
    """Pisahkan sebagian artikel bukti dari `source_signal` menjadi
    sinyal baru -- untuk kasus sistem keliru menggabungkan dua
    kejadian berbeda yang penyakit+lokasi+waktunya kebetulan mirip.
    """
    validate_active_user(actor, "Analis")

    if not reason.strip():
        raise ValidationError(
            "Alasan pemisahan wajib diisi untuk jejak audit."
        )
    if not new_title.strip() or not new_summary.strip():
        raise ValidationError(
            "Judul dan ringkasan sinyal baru wajib diisi."
        )

    links_to_move = list(
        source_signal.signal_articles.filter(
            article_id__in=article_ids
        )
    )

    if not links_to_move:
        raise ValidationError(
            "Pilih minimal satu artikel untuk dipisahkan."
        )

    remaining_count = source_signal.signal_articles.exclude(
        article_id__in=article_ids
    ).count()

    if remaining_count == 0:
        raise ValidationError(
            "Tidak bisa memisahkan semua artikel -- sinyal asal "
            "wajib menyisakan minimal satu artikel bukti. Kalau "
            "seluruh isi sinyal ini keliru, gunakan tolak sinyal."
        )

    from .generation import generate_signal_code  # hindari circular import

    new_signal = Signal.objects.create(
        code=generate_signal_code(),
        title=new_title.strip(),
        summary=new_summary.strip(),
        primary_disease=source_signal.primary_disease,
        primary_location=source_signal.primary_location,
        event_start_date=source_signal.event_start_date,
        event_end_date=source_signal.event_end_date,
        status=Signal.Status.NEEDS_REVIEW,
        priority_level=source_signal.priority_level,
        confidence_level=Signal.ConfidenceLevel.UNASSESSED,
        created_by_system=False,
    )

    for link in links_to_move:
        link.signal = new_signal
        link.save(update_fields=["signal"])

    record_signal_history(
        signal=source_signal,
        from_status=source_signal.status,
        to_status=source_signal.status,
        changed_by=actor,
        reason=(
            f"Sebagian artikel dipisahkan menjadi sinyal baru "
            f"{new_signal.code}. {reason.strip()}"
        ),
        metadata={
            "action": "split_from",
            "new_signal_code": new_signal.code,
            "articles_moved": len(links_to_move),
        },
    )

    record_signal_history(
        signal=new_signal,
        from_status="",
        to_status=Signal.Status.NEEDS_REVIEW,
        changed_by=actor,
        reason=(
            f"Dibentuk dari pemisahan sinyal {source_signal.code}. "
            f"{reason.strip()}"
        ),
        metadata={
            "action": "split_into",
            "source_signal_code": source_signal.code,
            "articles_moved": len(links_to_move),
        },
    )

    return new_signal
