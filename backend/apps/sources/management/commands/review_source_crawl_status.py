"""Nonaktifkan sumber yang terbukti/dicurigai diblokir (WAF/anti-bot),
dan aktifkan crawl_enabled untuk sumber yang secara teknis sudah siap
(seed + pola allow aktif, terverifikasi) tapi belum diaktifkan.

Dry-run secara default -- jalankan dengan --apply untuk benar-benar
menyimpan perubahan.
"""
from django.core.management.base import BaseCommand

from apps.dashboard.views import (
    _attach_source_readiness,
    _source_readiness_queryset,
)
from apps.sources.models import Source


# Kode sumber yang TERBUKTI diblokir dari log error asli (HTTP 403/468
# pada seed listing, dicek langsung dari runserver log).
CONFIRMED_BLOCKED = {
    "pikiran-rakyat": "HTTP 403 saat fetch seed listing (WAF/anti-bot).",
    "tvri": "HTTP 468 (kode non-standar, kemungkinan proteksi bot) saat fetch seed listing.",
}

# Kode sumber dengan GEJALA IDENTIK (1 kandidat, 1 gagal) di riwayat
# job -- pola yang sama seperti yang CONFIRMED di atas, tapi pesan
# error persisnya belum saya lihat langsung. Nonaktif sementara;
# cek "Detail" salah satu job-nya kalau mau tahu penyebab pastinya.
SUSPECTED_BLOCKED = {
    "suara-merdeka": "Pola gejala sama dengan sumber terblokir (1 kandidat, 1 gagal) -- belum dikonfirmasi pesan error persisnya.",
    "satu-data-indonesia": "Pola gejala sama dengan sumber terblokir (1 kandidat, 1 gagal) -- belum dikonfirmasi pesan error persisnya.",
    "media-indonesia": "Pola gejala sama dengan sumber terblokir (1 kandidat, 1 gagal) -- belum dikonfirmasi pesan error persisnya.",
    "klhk": "Pola gejala sama dengan sumber terblokir (1 kandidat, 1 gagal) -- belum dikonfirmasi pesan error persisnya.",
    "komdigi": "Pola gejala sama dengan sumber terblokir (1 kandidat, 1 gagal) -- belum dikonfirmasi pesan error persisnya.",
    "bps": "Pola gejala sama dengan sumber terblokir (1 kandidat, 1 gagal) -- belum dikonfirmasi pesan error persisnya.",
}

TO_DISABLE = {**CONFIRMED_BLOCKED, **SUSPECTED_BLOCKED}


class Command(BaseCommand):
    help = (
        "Nonaktifkan sumber bermasalah, aktifkan sumber yang siap. "
        "Dry-run secara default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Simpan perubahan (tanpa ini cuma preview).",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        self.stdout.write(
            self.style.WARNING("=== NONAKTIFKAN SUMBER BERMASALAH ===")
            if apply_changes
            else self.style.WARNING(
                "=== PREVIEW nonaktifkan sumber bermasalah (dry-run) ==="
            )
        )

        to_disable_sources = Source.objects.filter(
            code__in=TO_DISABLE.keys(),
            crawl_enabled=True,
        )

        for source in to_disable_sources:
            confirmed = source.code in CONFIRMED_BLOCKED
            label = "TERKONFIRMASI" if confirmed else "DICURIGAI"
            self.stdout.write(
                f"  - [{label}] {source.name} ({source.code}): "
                f"{TO_DISABLE[source.code]}"
            )

        disabled_count = to_disable_sources.count()

        if apply_changes and disabled_count:
            note_suffix = (
                "\nDinonaktifkan otomatis: seed listing gagal "
                "di-fetch berulang (kemungkinan diblokir WAF/anti-bot)."
            )
            for source in Source.objects.filter(
                code__in=TO_DISABLE.keys(),
                crawl_enabled=True,
            ):
                source.crawl_enabled = False
                source.crawler_notes = (
                    source.crawler_notes + note_suffix
                ).strip()
                source.save(
                    update_fields=["crawl_enabled", "crawler_notes", "updated_at"]
                )

        self.stdout.write(
            f"Total dinonaktifkan: {disabled_count}"
            + ("" if apply_changes else " (belum disimpan, pakai --apply)")
        )

        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING("=== AKTIFKAN SUMBER YANG SIAP ===")
            if apply_changes
            else self.style.WARNING(
                "=== PREVIEW aktifkan sumber yang siap (dry-run) ==="
            )
        )

        candidates = _source_readiness_queryset().filter(
            crawl_enabled=False,
        ).exclude(
            code__in=TO_DISABLE.keys(),
        )

        ready_to_enable = []
        for source in candidates:
            _attach_source_readiness(source)
            if source.crawl_readiness.is_ready:
                ready_to_enable.append(source)

        for source in ready_to_enable:
            self.stdout.write(f"  - {source.name} ({source.code})")

        if apply_changes:
            for source in ready_to_enable:
                source.crawl_enabled = True
                source.save(update_fields=["crawl_enabled", "updated_at"])

        self.stdout.write(
            f"Total diaktifkan: {len(ready_to_enable)}"
            + ("" if apply_changes else " (belum disimpan, pakai --apply)")
        )
