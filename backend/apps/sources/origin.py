"""Pengelompokan asal (domestik/internasional) untuk Source.

Berdasarkan audit data produksi: seluruh Source dengan
`source_type == INTERNATIONAL_MEDIA` adalah organisasi/media asing (WHO,
Reuters, BBC, US CDC, ECDC, dll) -- tidak ada satupun yang sebenarnya
media Indonesia. Empat kategori lain (government, national_media,
local_media, other) seluruhnya adalah entitas Indonesia meski banyak yang
memakai domain .com/.co.id/.tv/.id campur aduk (domain saja tidak bisa
dipakai sebagai penentu, makanya dikelompokkan lewat source_type).
"""
from __future__ import annotations

from django.db.models import QuerySet

from .models import Source


ORIGIN_CHOICES = (
    ("indonesia", "Indonesia"),
    ("international", "Internasional"),
)


def domestic_sources(queryset: QuerySet | None = None) -> QuerySet:
    """Source yang berasal dari Indonesia (semua source_type kecuali
    international_media)."""
    base = Source.objects.all() if queryset is None else queryset
    return base.exclude(
        source_type=Source.SourceType.INTERNATIONAL_MEDIA,
    )


def international_sources(queryset: QuerySet | None = None) -> QuerySet:
    """Source yang berasal dari luar Indonesia."""
    base = Source.objects.all() if queryset is None else queryset
    return base.filter(
        source_type=Source.SourceType.INTERNATIONAL_MEDIA,
    )


def apply_origin_filter(
    queryset: QuerySet,
    origin: str,
) -> QuerySet:
    """Terapkan filter asal ('indonesia'/'international') kalau valid,
    kembalikan queryset apa adanya kalau kosong/tidak dikenali."""
    if origin == "indonesia":
        return domestic_sources(queryset)
    if origin == "international":
        return international_sources(queryset)
    return queryset
