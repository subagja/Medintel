from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.sources.models import Source, SourceSeedUrl, SourceUrlPattern
from apps.sources.services import is_domain_allowed


@dataclass(frozen=True)
class AllowPatternPatch:
    pattern: str
    match_type: str = SourceUrlPattern.MatchType.REGEX
    description: str = ""


@dataclass(frozen=True)
class SourceDatabasePatch:
    code: str
    domain: str = ""
    base_url: str = ""
    allow_subdomains: bool | None = None
    allow_patterns: tuple[AllowPatternPatch, ...] = ()
    note: str = ""

    @property
    def changes_identity(self) -> bool:
        return bool(self.domain or self.base_url)


SOURCE_DATABASE_PATCHES: tuple[SourceDatabasePatch, ...] = (
    SourceDatabasePatch(
        code="cdc-us",
        allow_subdomains=True,
        allow_patterns=(
            AllowPatternPatch(
                pattern=r"^/eid/article/",
                description=(
                    "Izinkan artikel Emerging Infectious Diseases yang "
                    "ditautkan oleh RSS resmi CDC."
                ),
            ),
        ),
        note=(
            "RSS EID memakai subdomain resmi wwwnc.cdc.gov dan path "
            "/eid/article/."
        ),
    ),
    SourceDatabasePatch(
        code="who",
        allow_subdomains=True,
        allow_patterns=(
            AllowPatternPatch(
                pattern=r"^/(countries|health-topics|news|feature-stories)/",
                description=(
                    "Izinkan artikel resmi WHO regional yang ditautkan "
                    "oleh feed keadaan darurat dan wabah."
                ),
            ),
        ),
        note=(
            "Feed WHO regional berada pada subdomain resmi seperti "
            "afro.who.int."
        ),
    ),
    SourceDatabasePatch(
        code="detik",
        allow_subdomains=True,
        note="RSS berita Detik berada pada subdomain resmi news.detik.com.",
    ),
    SourceDatabasePatch(
        code="liputan6",
        allow_subdomains=True,
        note=(
            "Endpoint feed Liputan6 berada pada subdomain resmi "
            "feed.liputan6.com."
        ),
    ),
    SourceDatabasePatch(
        code="tempo",
        allow_subdomains=True,
        note="Endpoint feed Tempo berada pada subdomain resmi rss.tempo.co.",
    ),
    SourceDatabasePatch(
        code="republika",
        allow_subdomains=True,
        note=(
            "Artikel dari feed Republika dapat berada pada subdomain kanal "
            "resmi Republika."
        ),
    ),
    SourceDatabasePatch(
        code="tribunnews",
        allow_patterns=(
            AllowPatternPatch(
                pattern=r"/[0-9]{4}/[0-9]{2}/[0-9]{1,2}/",
                description=(
                    "Perbaiki pola tanggal URL artikel Tribunnews agar "
                    "menerima tanggal satu maupun dua digit."
                ),
            ),
        ),
        note=(
            "Pola tanggal lama hanya menerima satu digit hari sehingga URL "
            "RSS bertanggal 01-31 dapat tertolak."
        ),
    ),
    SourceDatabasePatch(
        code="fajar",
        domain="fajar.co.id",
        base_url="https://fajar.co.id",
        note=(
            "Identitas domain Fajar diperbarui dari fajaronline.co.id ke "
            "fajar.co.id sesuai endpoint RSS yang ditemukan."
        ),
    ),
    SourceDatabasePatch(
        code="cenderawasih-pos",
        domain="cenderawasihpos.jawapos.com",
        base_url="https://cenderawasihpos.jawapos.com",
        note=(
            "Identitas domain Cenderawasih Pos diperbarui dari ceposonline.com "
            "ke kanal resminya pada jaringan Jawa Pos."
        ),
    ),
    SourceDatabasePatch(
        code="surya",
        domain="surabaya.tribunnews.com",
        base_url="https://surabaya.tribunnews.com",
        note=(
            "Identitas domain Surya diperbarui dari surya.co.id ke kanal "
            "resminya pada jaringan Tribun."
        ),
    ),
)


class Command(BaseCommand):
    help = (
        "Perbaiki konfigurasi Source Master yang menghalangi audit RSS resmi. "
        "Command ini hanya mengubah data; tidak mengubah logika crawler."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            action="append",
            dest="source_codes",
            default=[],
            help=(
                "Kode Source yang diperbaiki. Dapat diulang. Jika kosong, "
                "seluruh patch aman diterapkan."
            ),
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Simpan perubahan ke database. Tanpa opsi ini command hanya "
                "menampilkan dry-run."
            ),
        )

    def handle(self, *args, **options):
        should_apply = options["apply"]
        requested_codes = tuple(dict.fromkeys(options["source_codes"]))
        patches = self._selected_patches(requested_codes)
        sources = {
            source.code: source
            for source in Source.objects.filter(
                code__in=[patch.code for patch in patches]
            ).prefetch_related("seed_urls", "url_patterns")
        }

        missing_codes = sorted(
            patch.code for patch in patches if patch.code not in sources
        )
        if missing_codes:
            raise CommandError(
                "Source tidak ditemukan: " + ", ".join(missing_codes)
            )

        self.stdout.write(
            self.style.MIGRATE_HEADING("Konfigurasi database RSS resmi")
        )
        self.stdout.write(
            f"Mode                  : {'APPLY' if should_apply else 'DRY-RUN'}"
        )
        self.stdout.write(f"Source diperbaiki     : {len(patches)}")
        self.stdout.write(
            "Batas tindakan         : data Source, subdomain, allow-pattern, "
            "dan seed lama yang tidak lagi sesuai domain"
        )

        changed_sources = 0
        changed_patterns = 0
        disabled_seeds = 0

        try:
            with transaction.atomic():
                for patch in patches:
                    source = sources[patch.code]
                    result = self._apply_patch(
                        source,
                        patch,
                        should_apply=should_apply,
                    )
                    changed_sources += result[0]
                    changed_patterns += result[1]
                    disabled_seeds += result[2]

                if not should_apply:
                    transaction.set_rollback(True)
        except ValidationError as exc:
            raise CommandError(
                "Konfigurasi Source tidak valid: " + str(exc)
            ) from exc

        self.stdout.write("")
        if should_apply:
            self.stdout.write(
                self.style.SUCCESS(
                    "Pembaruan database selesai. "
                    f"Source berubah: {changed_sources}; "
                    f"allow-pattern berubah: {changed_patterns}; "
                    f"seed domain lama dinonaktifkan: {disabled_seeds}."
                )
            )
            self.stdout.write(
                "Langkah berikutnya: jalankan activate_official_rss --apply "
                "untuk menguji feed secara live dan membuat seed RSS valid."
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "Dry-run selesai; database belum berubah. Tambahkan "
                    "--apply untuk menyimpan."
                )
            )

    @staticmethod
    def _selected_patches(
        requested_codes: tuple[str, ...],
    ) -> tuple[SourceDatabasePatch, ...]:
        by_code = {patch.code: patch for patch in SOURCE_DATABASE_PATCHES}

        unknown_codes = sorted(set(requested_codes) - set(by_code))
        if unknown_codes:
            raise CommandError(
                "Source tidak memiliki patch RSS aman: "
                + ", ".join(unknown_codes)
            )

        if not requested_codes:
            return SOURCE_DATABASE_PATCHES

        requested = set(requested_codes)
        return tuple(
            patch for patch in SOURCE_DATABASE_PATCHES if patch.code in requested
        )

    def _apply_patch(
        self,
        source: Source,
        patch: SourceDatabasePatch,
        *,
        should_apply: bool,
    ) -> tuple[int, int, int]:
        source_changed = False
        pattern_changes = 0
        seed_changes = 0
        planned: list[str] = []

        if patch.domain and source.domain != patch.domain:
            planned.append(f"domain {source.domain} -> {patch.domain}")
            source.domain = patch.domain
            source_changed = True

        if patch.base_url and source.base_url != patch.base_url:
            planned.append(f"base_url -> {patch.base_url}")
            source.base_url = patch.base_url
            source_changed = True

        if (
            patch.allow_subdomains is not None
            and source.allow_subdomains != patch.allow_subdomains
        ):
            planned.append(
                f"allow_subdomains -> {str(patch.allow_subdomains).lower()}"
            )
            source.allow_subdomains = patch.allow_subdomains
            source_changed = True

        if patch.changes_identity and source.crawl_enabled:
            planned.append("crawl_enabled -> false sampai audit RSS lulus")
            source.crawl_enabled = False
            source_changed = True

        note = (
            "Konfigurasi RSS database: " + patch.note.strip()
            if patch.note.strip()
            else ""
        )
        if note and note not in source.crawler_notes:
            source.crawler_notes = f"{source.crawler_notes}\n{note}".strip()
            planned.append("catatan audit ditambahkan")
            source_changed = True

        if source_changed and should_apply:
            source.save()

        for pattern_patch in patch.allow_patterns:
            pattern = source.url_patterns.filter(
                pattern_type=SourceUrlPattern.PatternType.ALLOW,
                match_type=pattern_patch.match_type,
                pattern=pattern_patch.pattern,
            ).first()

            if pattern is None:
                planned.append(f"allow-pattern + {pattern_patch.pattern}")
                pattern_changes += 1
                if should_apply:
                    SourceUrlPattern.objects.create(
                        source=source,
                        pattern_type=SourceUrlPattern.PatternType.ALLOW,
                        match_type=pattern_patch.match_type,
                        pattern=pattern_patch.pattern,
                        priority=100,
                        description=pattern_patch.description,
                        is_active=True,
                    )
            elif not pattern.is_active:
                planned.append(f"allow-pattern aktif {pattern_patch.pattern}")
                pattern_changes += 1
                if should_apply:
                    pattern.is_active = True
                    pattern.description = (
                        pattern_patch.description or pattern.description
                    )
                    pattern.save(
                        update_fields=[
                            "is_active",
                            "description",
                            "updated_at",
                        ]
                    )

        if patch.changes_identity:
            for seed in source.seed_urls.filter(is_active=True):
                if self._domain_matches(source, seed.url):
                    continue

                planned.append(f"seed lama nonaktif {seed.url}")
                seed_changes += 1
                if should_apply:
                    reason = (
                        "Dinonaktifkan karena domain Source diperbarui; "
                        "menunggu seed baru yang lolos audit."
                    )
                    notes = seed.notes
                    if reason not in notes:
                        notes = f"{notes}\n{reason}".strip()

                    # Model menolak penyimpanan seed lintas-domain meski seed
                    # sedang dinonaktifkan. Sesudah identitas Source berubah,
                    # update terarah diperlukan agar record audit lama tetap
                    # tersimpan tetapi tidak lagi dipakai crawler.
                    SourceSeedUrl.objects.filter(pk=seed.pk).update(
                        is_active=False,
                        notes=notes,
                        updated_at=timezone.now(),
                    )

        status = "CHANGE" if planned else "UNCHANGED"
        self.stdout.write("")
        self.stdout.write(
            self.style.HTTP_INFO(f"[{status}] {source.code} — {source.name}")
        )
        for item in planned:
            self.stdout.write(f"  - {item}")
        if not planned:
            self.stdout.write("  - konfigurasi sudah sesuai")

        return int(bool(source_changed)), pattern_changes, seed_changes

    @staticmethod
    def _domain_matches(source: Source, url: str) -> bool:
        """Gunakan policy domain baru tanpa memerlukan status readiness."""
        if is_domain_allowed(source, url):
            return True

        hostname = (urlsplit(url).hostname or "").casefold()
        return hostname.removeprefix("www.") == source.domain.casefold()
