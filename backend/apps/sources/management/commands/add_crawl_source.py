import re
from urllib.parse import urlsplit

from django.core.management.base import (
    BaseCommand,
    CommandError,
)
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.crawlers.real_crawler import (
    GenericHtmlCrawler,
)
from apps.crawlers.services import (
    run_crawler,
)
from apps.sources.models import (
    Source,
    SourceSeedUrl,
    SourceUrlPattern,
)
from apps.sources.services import (
    check_source_crawl_readiness,
    normalize_domain,
    normalize_url,
)


SOURCE_TYPE_CHOICES = {
    value
    for value, _label
    in Source.SourceType.choices
}

SEED_TYPE_CHOICES = {
    value
    for value, _label
    in SourceSeedUrl.SeedType.choices
}


COMMON_DENY_REGEX = (
    r"^/(?:"
    r"search|pencarian|tag|tags|author|authors|"
    r"login|logout|register|registration|profile|"
    r"contact|kontak|about|tentang|privacy|terms|"
    r"video|videos|audio|podcast|gallery|galeri|"
    r"foto|photo|newsletter|subscription"
    r")(?:/|$)"
)


def build_direct_allow_regex(
    normalized_url: str,
) -> str:
    """
    Membuat allow pattern khusus untuk satu URL artikel langsung.

    Query parameter tidak dimasukkan agar URL tetap cocok setelah
    proses normalisasi.
    """
    parsed = urlsplit(
        normalized_url
    )

    path = parsed.path or "/"
    escaped_path = re.escape(
        path.rstrip("/")
    )

    if not escaped_path:
        return r"^/$"

    return rf"^{escaped_path}/?$"


def normalize_article_path(
    article_path: str,
) -> str:
    cleaned = (
        article_path or ""
    ).strip()

    if not cleaned:
        return ""

    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"

    cleaned = re.sub(
        r"/{2,}",
        "/",
        cleaned,
    )

    if cleaned != "/":
        cleaned = cleaned.rstrip("/")

    return cleaned


def build_listing_allow_regex(
    article_path: str,
) -> str:
    """
    Membuat allow pattern berdasarkan awalan path artikel.

    Contoh:
    /berita
    → ^/berita(?:/|$)

    /id
    → ^/id(?:/|$)
    """
    normalized_path = normalize_article_path(
        article_path
    )

    if not normalized_path:
        raise CommandError(
            (
                "Seed listing memerlukan "
                "--article-path atau --allow-regex."
            )
        )

    escaped_path = re.escape(
        normalized_path
    )

    return rf"^{escaped_path}(?:/|$)"


class Command(BaseCommand):
    help = (
        "Mendaftarkan source crawler, seed URL, "
        "allow pattern, dan deny pattern dalam satu perintah."
    )

    def add_arguments(
        self,
        parser,
    ):
        parser.add_argument(
            "--name",
            required=True,
            help=(
                "Nama sumber, misalnya "
                "'Radio Republik Indonesia'."
            ),
        )

        parser.add_argument(
            "--code",
            required=True,
            help=(
                "Kode unik sumber, misalnya rri."
            ),
        )

        parser.add_argument(
            "--url",
            required=True,
            help=(
                "Seed URL berupa artikel langsung "
                "atau halaman daftar artikel."
            ),
        )

        parser.add_argument(
            "--type",
            dest="source_type",
            default=(
                Source.SourceType.NATIONAL_MEDIA
            ),
            choices=sorted(
                SOURCE_TYPE_CHOICES
            ),
            help=(
                "Jenis sumber. Default: national_media."
            ),
        )

        parser.add_argument(
            "--seed-type",
            default=(
                SourceSeedUrl.SeedType.DIRECT
            ),
            choices=sorted(
                SEED_TYPE_CHOICES
            ),
            help=(
                "Jenis seed URL. Default: direct."
            ),
        )

        parser.add_argument(
            "--article-path",
            default="",
            help=(
                "Awalan path artikel untuk seed listing, "
                "misalnya /berita, /id, atau /kesehatan."
            ),
        )

        parser.add_argument(
            "--allow-regex",
            default="",
            help=(
                "Regex allow khusus. Menggantikan "
                "pola otomatis dari --article-path."
            ),
        )

        parser.add_argument(
            "--allow-subdomains",
            action="store_true",
            help=(
                "Izinkan URL dari subdomain sumber."
            ),
        )

        parser.add_argument(
            "--max-articles",
            type=int,
            default=50,
            help=(
                "Jumlah maksimum artikel per proses. "
                "Default: 50."
            ),
        )

        parser.add_argument(
            "--delay",
            type=float,
            default=1.0,
            help=(
                "Jeda antar-request dalam detik. "
                "Default: 1.0."
            ),
        )

        parser.add_argument(
            "--timeout",
            type=int,
            default=20,
            help=(
                "Timeout request dalam detik. "
                "Default: 20."
            ),
        )

        parser.add_argument(
            "--notes",
            default="",
            help=(
                "Catatan teknis source."
            ),
        )

        parser.add_argument(
            "--crawl",
            action="store_true",
            help=(
                "Langsung menjalankan crawler "
                "setelah source selesai dibuat."
            ),
        )

        parser.add_argument(
            "--limit",
            type=int,
            default=1,
            help=(
                "Batas artikel ketika memakai --crawl. "
                "Default: 1."
            ),
        )

    def handle(
        self,
        *args,
        **options,
    ):
        name = options["name"].strip()
        code = options["code"].strip().lower()
        raw_url = options["url"].strip()
        source_type = options["source_type"]
        seed_type = options["seed_type"]
        article_path = options["article_path"]
        custom_allow_regex = (
            options["allow_regex"].strip()
        )
        allow_subdomains = options[
            "allow_subdomains"
        ]
        max_articles = options["max_articles"]
        delay = options["delay"]
        timeout = options["timeout"]
        notes = options["notes"].strip()
        should_crawl = options["crawl"]
        crawl_limit = options["limit"]

        if not name:
            raise CommandError(
                "--name tidak boleh kosong."
            )

        if not code:
            raise CommandError(
                "--code tidak boleh kosong."
            )

        if max_articles < 1:
            raise CommandError(
                "--max-articles minimal 1."
            )

        if delay < 0:
            raise CommandError(
                "--delay tidak boleh negatif."
            )

        if timeout < 1:
            raise CommandError(
                "--timeout minimal 1."
            )

        if crawl_limit < 1:
            raise CommandError(
                "--limit minimal 1."
            )

        try:
            normalized_seed_url = normalize_url(
                raw_url
            )
        except ValueError as exc:
            raise CommandError(
                str(exc)
            ) from exc

        parsed_url = urlsplit(
            normalized_seed_url
        )

        hostname = parsed_url.hostname

        if not hostname:
            raise CommandError(
                "URL tidak memiliki domain valid."
            )

        domain = normalize_domain(
            hostname
        )

        base_url = (
            f"{parsed_url.scheme}://{domain}"
        )

        if custom_allow_regex:
            try:
                re.compile(
                    custom_allow_regex
                )
            except re.error as exc:
                raise CommandError(
                    (
                        "Nilai --allow-regex "
                        f"tidak valid: {exc}"
                    )
                ) from exc

            allow_regex = custom_allow_regex

        elif (
            seed_type
            == SourceSeedUrl.SeedType.DIRECT
        ):
            allow_regex = (
                build_direct_allow_regex(
                    normalized_seed_url
                )
            )

        elif seed_type in {
            SourceSeedUrl.SeedType.LISTING,
            SourceSeedUrl.SeedType.SITEMAP,
            SourceSeedUrl.SeedType.RSS,
        }:
            allow_regex = (
                build_listing_allow_regex(
                    article_path
                )
            )

        else:
            raise CommandError(
                (
                    "Seed type belum didukung "
                    "untuk pembuatan pola otomatis."
                )
            )

        try:
            with transaction.atomic():
                source, source_created = (
                    Source.objects.update_or_create(
                        code=code,
                        defaults={
                            "name": name,
                            "domain": domain,
                            "base_url": base_url,
                            "source_type": (
                                source_type
                            ),
                            "is_verified": True,
                            "is_active": True,
                            "verification_notes": (
                                "Didaftarkan melalui "
                                "management command "
                                "add_crawl_source."
                            ),
                            "verified_at": (
                                timezone.now()
                            ),
                            "crawl_enabled": True,
                            "crawl_strategy": (
                                Source.CrawlStrategy.HTML
                            ),
                            "allow_subdomains": (
                                allow_subdomains
                            ),
                            "max_articles_per_run": (
                                max_articles
                            ),
                            "request_delay_seconds": (
                                delay
                            ),
                            "request_timeout_seconds": (
                                timeout
                            ),
                            "crawler_notes": notes,
                        },
                    )
                )

                seed, seed_created = (
                    SourceSeedUrl.objects.update_or_create(
                        source=source,
                        url=normalized_seed_url,
                        defaults={
                            "seed_type": seed_type,
                            "priority": 10,
                            "is_active": True,
                            "notes": (
                                "Dibuat otomatis melalui "
                                "add_crawl_source."
                            ),
                        },
                    )
                )

                allow_pattern, allow_created = (
                    SourceUrlPattern.objects.update_or_create(
                        source=source,
                        pattern_type=(
                            SourceUrlPattern.PatternType.ALLOW
                        ),
                        match_type=(
                            SourceUrlPattern.MatchType.REGEX
                        ),
                        pattern=allow_regex,
                        defaults={
                            "priority": 10,
                            "description": (
                                "Allow pattern otomatis"
                            ),
                            "is_active": True,
                        },
                    )
                )

                deny_pattern, deny_created = (
                    SourceUrlPattern.objects.update_or_create(
                        source=source,
                        pattern_type=(
                            SourceUrlPattern.PatternType.DENY
                        ),
                        match_type=(
                            SourceUrlPattern.MatchType.REGEX
                        ),
                        pattern=COMMON_DENY_REGEX,
                        defaults={
                            "priority": 1,
                            "description": (
                                "Deny pattern umum otomatis"
                            ),
                            "is_active": True,
                        },
                    )
                )

        except IntegrityError as exc:
            raise CommandError(
                (
                    "Gagal menyimpan source. "
                    "Kemungkinan nama atau domain sudah "
                    "dipakai oleh source dengan kode lain."
                )
            ) from exc

        readiness = check_source_crawl_readiness(
            source
        )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "=== SOURCE BERHASIL DIKONFIGURASI ==="
            )
        )

        self.stdout.write(
            (
                f"Source       : {source.name} "
                f"({source.code})"
            )
        )
        self.stdout.write(
            f"Domain       : {source.domain}"
        )
        self.stdout.write(
            f"Base URL     : {source.base_url}"
        )
        self.stdout.write(
            f"Jenis source : {source.source_type}"
        )
        self.stdout.write(
            (
                f"Seed URL     : {seed.url} "
                f"({seed.seed_type})"
            )
        )
        self.stdout.write(
            f"Allow regex  : {allow_pattern.pattern}"
        )
        self.stdout.write(
            f"Deny regex   : {deny_pattern.pattern}"
        )
        self.stdout.write(
            (
                "Source baru  : "
                f"{source_created}"
            )
        )
        self.stdout.write(
            (
                "Seed baru    : "
                f"{seed_created}"
            )
        )
        self.stdout.write(
            (
                "Allow baru   : "
                f"{allow_created}"
            )
        )
        self.stdout.write(
            (
                "Deny baru    : "
                f"{deny_created}"
            )
        )

        if readiness.is_ready:
            self.stdout.write(
                self.style.SUCCESS(
                    "Crawler ready: YA"
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR(
                    "Crawler ready: TIDAK"
                )
            )

            for error in readiness.errors:
                self.stdout.write(
                    self.style.ERROR(
                        f"- {error}"
                    )
                )

            raise CommandError(
                "Konfigurasi source belum siap."
            )

        if not should_crawl:
            self.stdout.write("")
            self.stdout.write(
                (
                    "Source sudah siap. Jalankan crawler "
                    f"dengan: python manage.py "
                    f"run_html_crawler --source "
                    f"{source.code} --limit 1"
                )
            )
            return

        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                (
                    "Menjalankan crawler langsung "
                    f"untuk source {source.code}..."
                )
            )
        )

        crawler = GenericHtmlCrawler(
            source_code=source.code,
            limit=crawl_limit,
        )

        try:
            result = run_crawler(
                crawler,
                trigger_type="manual_command",
            )
        except ValueError as exc:
            raise CommandError(
                str(exc)
            ) from exc

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "=== HASIL CRAWLER ==="
            )
        )
        self.stdout.write(
            f"Artikel ditemukan : {result.total_found}"
        )
        self.stdout.write(
            f"Artikel baru      : {result.total_created}"
        )
        self.stdout.write(
            f"Artikel duplikat  : {result.total_duplicate}"
        )
        self.stdout.write(
            f"Artikel ditolak   : {result.total_rejected}"
        )
        self.stdout.write(
            f"Artikel gagal     : {result.total_failed}"
        )