# Data migration: aktifkan crawl_enabled untuk sumber Indonesia mainstream
# yang SUDAH lengkap secara teknis (is_active=True, is_verified=True,
# punya seed URL aktif, dan punya allow-pattern aktif) tapi crawl_enabled
# masih False -- kemungkinan besar kelewat diaktifkan saat konfigurasi awal.
#
# Daftar 35 kode ini dipilih lewat audit langsung ke data produksi (bukan
# tebakan): setiap kode di bawah sudah memenuhi SEMUA syarat
# check_source_crawl_readiness() KECUALI crawl_enabled, jadi aman untuk
# diaktifkan tanpa risiko crawler jalan tanpa arah (nyasar ke halaman
# bukan-artikel) karena allow/deny pattern-nya sudah ada dan aktif.
#
# Sumber yang BELUM punya seed URL / allow pattern sama sekali (mis.
# Okezone, Kumparan, Sindonews, dan sebagian besar jaringan Tribun/Radar
# daerah) SENGAJA tidak dimasukkan di sini -- mengaktifkan crawling tanpa
# pola URL yang teruji berisiko crawler nyasar atau membuang waktu di
# halaman yang tidak relevan. Itu perlu proses setup+verifikasi terpisah.

from django.db import migrations


# Media nasional & kanal kesehatan mainstream yang diakses luas
# masyarakat Indonesia.
NATIONAL_MEDIA_CODES = [
    "detik",
    "kompas",
    "tribunnews",
    "republika",
    "tvri",
    "voa-indonesia",
    "katadata",
    "liputan6-health",
]

# Media daerah (jaringan Tribun/Kompas regional serta koran daerah
# established) yang sudah lengkap konfigurasinya.
REGIONAL_MEDIA_CODES = [
    "tribun-jabar",
    "kompas-bandung",
    "tribun-jateng",
    "suara-merdeka",
    "surya",
    "tribun-jatim",
    "bali-post",
    "fajar",
    "tribun-timur",
    "cenderawasih-pos",
]

# Instansi pemerintah RI (kementerian/lembaga) -- sumber primer resmi,
# relevan langsung untuk konteks kebijakan & data surveilans.
GOVERNMENT_CODES = [
    "ayo-sehat",
    "bkpk-kemkes",
    "satusehat",
    "bpom",
    "kementan",
    "bnpb",
    "bmkg",
    "bps",
    "satu-data-indonesia",
    "klhk",
    "brin",
    "kemlu",
    "kemenhub",
    "imigrasi",
    "komdigi",
    "polri",
    "karantina-indonesia",
]

CODES_TO_ENABLE = (
    NATIONAL_MEDIA_CODES
    + REGIONAL_MEDIA_CODES
    + GOVERNMENT_CODES
)


def enable_mainstream_indonesia_sources(apps, schema_editor):
    Source = apps.get_model("sources", "Source")
    SourceSeedUrl = apps.get_model("sources", "SourceSeedUrl")
    SourceUrlPattern = apps.get_model("sources", "SourceUrlPattern")

    note = (
        "crawl_enabled diaktifkan otomatis: sumber sudah terverifikasi, "
        "aktif, dan memiliki seed URL + allow pattern aktif "
        "(kurasi sumber mainstream Indonesia)."
    )

    for source in Source.objects.filter(code__in=CODES_TO_ENABLE):
        has_active_seed = SourceSeedUrl.objects.filter(
            source=source, is_active=True,
        ).exists()
        has_active_allow = SourceUrlPattern.objects.filter(
            source=source, is_active=True, pattern_type="allow",
        ).exists()

        # Pengaman ganda: walau daftar kode sudah diaudit, tetap jangan
        # aktifkan kalau ternyata syaratnya tidak terpenuhi di database
        # yang sedang dijalankan migration ini (mis. environment lain).
        if not (
            source.is_active
            and source.is_verified
            and has_active_seed
            and has_active_allow
        ):
            continue

        if source.crawl_enabled:
            continue

        source.crawl_enabled = True

        if note not in source.crawler_notes:
            source.crawler_notes = (
                f"{source.crawler_notes}\n{note}"
            ).strip()

        source.save(
            update_fields=["crawl_enabled", "crawler_notes", "updated_at"],
        )


def revert_mainstream_indonesia_sources(apps, schema_editor):
    # Sengaja tidak mengembalikan crawl_enabled ke False -- kalau operator
    # sempat menjalankan crawl beneran setelah migration ini, kita tidak
    # mau diam-diam mematikan sumber yang sudah terbukti jalan hanya
    # karena migration di-rollback. Reversal manual lewat admin/UI.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sources", "0002_sourceseedurl_alter_sourceurlpattern_options_and_more"),
    ]

    operations = [
        migrations.RunPython(
            enable_mainstream_indonesia_sources,
            revert_mainstream_indonesia_sources,
        ),
    ]
