# Data migration: draft konfigurasi crawler (seed URL + pola allow/deny)
# untuk 16 media nasional besar yang diakses luas masyarakat Indonesia,
# tapi di database saat ini SAMA SEKALI belum punya seed URL atau pola URL.
#
# PENTING -- ini draft, BUKAN aktivasi:
# - is_verified TETAP False, crawl_enabled TETAP False.
# - Pola URL disusun dari pengetahuan umum tentang struktur URL tiap
#   situs (bukan hasil uji fetch langsung, sandbox ini tidak punya akses
#   internet), sehingga akurasinya bervariasi per sumber. Level keyakinan
#   dicatat di `description` tiap pola -- perhatikan terutama yang
#   ditandai "keyakinan rendah" (Kumparan, CNBC Indonesia, Narasi):
#   struktur URL mereka tidak memakai kata kunci jalur yang jelas
#   (semacam /berita/ atau /read/), jadi pola regexnya lebih rapuh.
# - WAJIB coba "Jalankan Crawler" secara manual dulu untuk tiap sumber
#   (lewat dashboard, cukup 1 sumber per percobaan) dan periksa jumlah
#   KANDIDAT vs DITOLAK sebelum set is_verified=True & crawl_enabled=True
#   lewat halaman Edit Sumber.

from django.db import migrations


# Pola deny generik yang sudah terbukti dipakai di sumber lain pada
# database ini (halaman non-artikel: tag/pencarian/profil, media
# video/foto/galeri, dan berkas statis).
GENERIC_DENY_PATTERNS = [
    {
        "pattern": (
            r"/(tag|tags|search|cari|pencarian|author|penulis|"
            r"profile|profil|about|tentang-kami|contact|kontak|"
            r"login|register|privacy|kebijakan-privasi|terms|"
            r"syarat-ketentuan|subscribe|newsletter)(/|$|\?)"
        ),
        "description": (
            "Tolak halaman pencarian, tag, profil, autentikasi, "
            "dan halaman korporat/nonartikel."
        ),
    },
    {
        "pattern": (
            r"/(video|videos|foto|photo|photos|galeri|gallery|"
            r"podcast|live|amp|infografis)(/|$)"
        ),
        "description": (
            "Tolak video, foto, galeri, podcast, live, dan "
            "halaman AMP sebagai target artikel utama."
        ),
    },
    {
        "pattern": (
            r"\.(jpg|jpeg|png|gif|webp|svg|ico|css|js|pdf|zip|"
            r"rar|mp3|mp4|avi|mov|wmv|doc|docx|xls|xlsx|ppt|pptx)"
            r"(\?|$)"
        ),
        "description": (
            "Tolak aset statis dan berkas unduhan yang bukan "
            "halaman artikel HTML."
        ),
    },
]


# code -> {seed_url, allow_patterns: [{pattern, description, confidence}]}
DRAFT_CONFIG = {
    "okezone": {
        "seed_url": "https://news.okezone.com",
        "allow": [
            (r"/read/", "Segmen /read/ konsisten di seluruh kanal Okezone.", "tinggi"),
        ],
    },
    "sindonews": {
        "seed_url": "https://nasional.sindonews.com",
        "allow": [
            (r"/read/", "Segmen /read/ konsisten di seluruh kanal SINDOnews.", "tinggi"),
        ],
    },
    "suara": {
        "seed_url": "https://www.suara.com",
        "allow": [
            (r"/\d{4}/\d{2}/\d{2}/", "URL Suara.com memakai format tanggal /YYYY/MM/DD/.", "sedang"),
        ],
    },
    "viva": {
        "seed_url": "https://www.viva.co.id",
        "allow": [
            (r"/berita/", "Segmen /berita/ pada URL artikel VIVA.", "sedang"),
        ],
    },
    "kontan": {
        "seed_url": "https://nasional.kontan.co.id",
        "allow": [
            (r"/news/", "Segmen /news/ pada URL artikel Kontan.", "sedang"),
        ],
    },
    "bisnis-indonesia": {
        "seed_url": "https://www.bisnis.com",
        "allow": [
            (r"/read/", "Segmen /read/ pada URL artikel Bisnis.com.", "sedang"),
        ],
    },
    "cnbc-indonesia": {
        "seed_url": "https://www.cnbcindonesia.com/news",
        "allow": [
            (
                r"-\d{1,2}-\d{5,}",
                (
                    "URL CNBC Indonesia berformat "
                    "<timestamp>-<kanal>-<id> tanpa kata kunci jalur "
                    "yang jelas; keyakinan rendah, wajib uji manual."
                ),
                "rendah",
            ),
        ],
    },
    "merdeka": {
        "seed_url": "https://www.merdeka.com",
        "allow": [
            (r"-\d{5,}\.html$", "URL artikel Merdeka.com diakhiri -<id>.html.", "sedang"),
        ],
    },
    "idn-times": {
        "seed_url": "https://www.idntimes.com/news",
        "allow": [
            (r"/(news|health|business|life|hype)/", "Kanal utama IDN Times yang relevan surveilans.", "sedang"),
        ],
    },
    "narasi": {
        "seed_url": "https://narasi.tv",
        "allow": [
            (
                r"/artikel/",
                (
                    "Dugaan segmen /artikel/ pada URL Narasi; belum "
                    "pernah diverifikasi, keyakinan rendah."
                ),
                "rendah",
            ),
        ],
    },
    "jakarta-post": {
        "seed_url": "https://www.thejakartapost.com/indonesia",
        "allow": [
            (r"/\d{4}/\d{2}/\d{2}/", "URL The Jakarta Post memakai format tanggal /YYYY/MM/DD/.", "sedang"),
        ],
    },
    "jakarta-globe": {
        "seed_url": "https://jakartaglobe.id/news",
        "allow": [
            (r"/news/", "Segmen /news/ pada URL artikel Jakarta Globe.", "sedang"),
        ],
    },
    "metrotvnews": {
        "seed_url": "https://www.metrotvnews.com",
        "allow": [
            (r"/read/", "Segmen /read/ pada URL artikel Metro TV News.", "sedang"),
        ],
    },
    "beritasatu": {
        "seed_url": "https://www.beritasatu.com",
        "allow": [
            (r"/\d{6,}/", "ID numerik panjang pada URL artikel BeritaSatu.", "sedang"),
        ],
    },
    "kumparan": {
        "seed_url": "https://kumparan.com",
        "allow": [
            (
                r"-[a-zA-Z0-9]{6,}$",
                (
                    "URL Kumparan berformat /channel/judul-slug-<id>; "
                    "pola akhiran alfanumerik ini rentan false positive, "
                    "keyakinan rendah, wajib uji manual."
                ),
                "rendah",
            ),
        ],
    },
    "conversation-id": {
        "seed_url": "https://theconversation.com/id",
        "allow": [
            (
                r"theconversation\.com/[a-z0-9-]+-\d{5,}$",
                (
                    "URL The Conversation berakhiran -<id numerik>; "
                    "situs global bukan spesifik Indonesia, prioritas "
                    "rendah untuk surveilans."
                ),
                "sedang",
            ),
        ],
    },
}


def add_draft_seed_and_patterns(apps, schema_editor):
    Source = apps.get_model("sources", "Source")
    SourceSeedUrl = apps.get_model("sources", "SourceSeedUrl")
    SourceUrlPattern = apps.get_model("sources", "SourceUrlPattern")

    note = (
        "Draft seed URL & pola URL ditambahkan otomatis (kurasi media "
        "nasional besar). BELUM diverifikasi -- uji \"Jalankan Crawler\" "
        "manual dan periksa hasilnya sebelum mengaktifkan sumber ini."
    )

    for code, config in DRAFT_CONFIG.items():
        source = Source.objects.filter(code=code).first()

        if source is None:
            continue

        # Jangan timpa kalau ternyata sumber ini sudah punya seed/pola
        # aktif (mis. sudah pernah dikonfigurasi manual sebelumnya).
        if source.seed_urls.filter(is_active=True).exists():
            continue
        if source.url_patterns.filter(
            is_active=True, pattern_type="allow",
        ).exists():
            continue

        SourceSeedUrl.objects.update_or_create(
            source=source,
            url=config["seed_url"],
            defaults={
                "seed_type": "listing",
                "priority": 100,
                "is_active": True,
                "notes": "Seed draft, perlu verifikasi.",
            },
        )

        for pattern, description, confidence in config["allow"]:
            SourceUrlPattern.objects.update_or_create(
                source=source,
                pattern_type="allow",
                match_type="regex",
                pattern=pattern,
                defaults={
                    "priority": 100,
                    "description": (
                        f"[keyakinan {confidence}] {description}"
                    ),
                    "is_active": True,
                },
            )

        for deny in GENERIC_DENY_PATTERNS:
            SourceUrlPattern.objects.update_or_create(
                source=source,
                pattern_type="deny",
                match_type="regex",
                pattern=deny["pattern"],
                defaults={
                    "priority": 10,
                    "description": deny["description"],
                    "is_active": True,
                },
            )

        if note not in source.crawler_notes:
            source.crawler_notes = (
                f"{source.crawler_notes}\n{note}"
            ).strip()
            source.save(update_fields=["crawler_notes", "updated_at"])


def remove_draft_seed_and_patterns(apps, schema_editor):
    # Sengaja tidak menghapus apapun saat rollback -- kalau sudah sempat
    # diverifikasi/diedit manual oleh operator, migration reverse tidak
    # boleh menghapus kerja itu. Pembersihan draft yang belum dipakai
    # dilakukan manual lewat halaman Edit Sumber kalau diperlukan.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sources", "0003_enable_mainstream_indonesia_sources"),
    ]

    operations = [
        migrations.RunPython(
            add_draft_seed_and_patterns,
            remove_draft_seed_and_patterns,
        ),
    ]
