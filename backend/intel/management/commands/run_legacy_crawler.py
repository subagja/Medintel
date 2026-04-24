from django.core.management.base import BaseCommand

from intel.models import Location, LocationAlias, ScoringRule
from intel.services.legacy_crawler_adapter import run_legacy_crawler_ingest


class Command(BaseCommand):
    help = "Run legacy crawler and ingest results into Signal / SignalLocation"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run-bootstrap",
            action="store_true",
            help="Only bootstrap minimal reference data without running crawler.",
        )

    def handle(self, *args, **options):
        self.stdout.write("Checking minimal reference data...")
        bootstrap_minimal_reference_data()
        self.stdout.write(self.style.SUCCESS("Minimal reference data ready."))

        if options["dry_run_bootstrap"]:
            self.stdout.write(
                self.style.WARNING("Dry run selesai. Crawler tidak dijalankan.")
            )
            return

        result = run_legacy_crawler_ingest()

        self.stdout.write(self.style.SUCCESS("=== LEGACY CRAWLER INGEST SUMMARY ==="))
        self.stdout.write(f"Total rows         : {result['total_rows']}")
        self.stdout.write(f"Created signals    : {result['created']}")
        self.stdout.write(f"Updated signals    : {result['updated']}")
        self.stdout.write(f"Matched locations  : {result['matched_locations']}")


def bootstrap_minimal_reference_data():
    """
    Membuat data awal minimal agar crawler bisa langsung jalan
    walaupun database benar-benar kosong.

    Isi bootstrap:
    1. Provinsi
    2. Kabupaten/kota prioritas
    3. Alias provinsi
    4. Alias kabupaten/kota
    5. Scoring rules dasar
    """

    # =========================================================
    # 1. Minimal Province Gazetteer
    # =========================================================
    provinces = [
        ("aceh", "Aceh"),
        ("sumatera_utara", "Sumatera Utara"),
        ("sumatera_barat", "Sumatera Barat"),
        ("riau", "Riau"),
        ("jambi", "Jambi"),
        ("sumatera_selatan", "Sumatera Selatan"),
        ("bengkulu", "Bengkulu"),
        ("lampung", "Lampung"),
        ("kepulauan_bangka_belitung", "Kepulauan Bangka Belitung"),
        ("kepulauan_riau", "Kepulauan Riau"),
        ("dki_jakarta", "DKI Jakarta"),
        ("jawa_barat", "Jawa Barat"),
        ("jawa_tengah", "Jawa Tengah"),
        ("di_yogyakarta", "DI Yogyakarta"),
        ("jawa_timur", "Jawa Timur"),
        ("banten", "Banten"),
        ("bali", "Bali"),
        ("nusa_tenggara_barat", "Nusa Tenggara Barat"),
        ("nusa_tenggara_timur", "Nusa Tenggara Timur"),
        ("kalimantan_barat", "Kalimantan Barat"),
        ("kalimantan_tengah", "Kalimantan Tengah"),
        ("kalimantan_selatan", "Kalimantan Selatan"),
        ("kalimantan_timur", "Kalimantan Timur"),
        ("kalimantan_utara", "Kalimantan Utara"),
        ("sulawesi_utara", "Sulawesi Utara"),
        ("sulawesi_tengah", "Sulawesi Tengah"),
        ("sulawesi_selatan", "Sulawesi Selatan"),
        ("sulawesi_tenggara", "Sulawesi Tenggara"),
        ("gorontalo", "Gorontalo"),
        ("sulawesi_barat", "Sulawesi Barat"),
        ("maluku", "Maluku"),
        ("maluku_utara", "Maluku Utara"),
        ("papua", "Papua"),
        ("papua_barat", "Papua Barat"),
        ("papua_selatan", "Papua Selatan"),
        ("papua_tengah", "Papua Tengah"),
        ("papua_pegunungan", "Papua Pegunungan"),
        ("papua_barat_daya", "Papua Barat Daya"),
    ]

    for code, name in provinces:
        Location.objects.update_or_create(
            level="province",
            province_code=code,
            defaults={
                "name": name,
                "display_name": name,
                "normalized_name": code,
                "city_regency_code": "",
                "parent": None,
                "is_active": True,
                "is_false_positive": False,
            },
        )

    # =========================================================
    # 1B. Minimal City/Regency Gazetteer
    # =========================================================
    city_regencies = [
        # Banten
        ("banten", "city", "Kota Cilegon", "cilegon", "36.72"),
        ("banten", "city", "Kota Serang", "serang", "36.73"),
        ("banten", "city", "Kota Tangerang", "tangerang", "36.71"),
        ("banten", "city", "Kota Tangerang Selatan", "tangerang_selatan", "36.74"),
        ("banten", "regency", "Kabupaten Serang", "kabupaten_serang", "36.04"),
        ("banten", "regency", "Kabupaten Tangerang", "kabupaten_tangerang", "36.03"),
        ("banten", "regency", "Kabupaten Pandeglang", "pandeglang", "36.01"),
        ("banten", "regency", "Kabupaten Lebak", "lebak", "36.02"),

        # DKI Jakarta
        ("dki_jakarta", "city", "Jakarta Selatan", "jakarta_selatan", "31.74"),
        ("dki_jakarta", "city", "Jakarta Timur", "jakarta_timur", "31.75"),
        ("dki_jakarta", "city", "Jakarta Pusat", "jakarta_pusat", "31.71"),
        ("dki_jakarta", "city", "Jakarta Barat", "jakarta_barat", "31.73"),
        ("dki_jakarta", "city", "Jakarta Utara", "jakarta_utara", "31.72"),
        ("dki_jakarta", "regency", "Kepulauan Seribu", "kepulauan_seribu", "31.01"),

        # Jawa Barat
        ("jawa_barat", "city", "Kota Bandung", "bandung", "32.73"),
        ("jawa_barat", "regency", "Kabupaten Bandung", "kabupaten_bandung", "32.04"),
        ("jawa_barat", "regency", "Kabupaten Bandung Barat", "kabupaten_bandung_barat", "32.17"),
        ("jawa_barat", "city", "Kota Bogor", "bogor", "32.71"),
        ("jawa_barat", "regency", "Kabupaten Bogor", "kabupaten_bogor", "32.01"),
        ("jawa_barat", "city", "Kota Bekasi", "bekasi", "32.75"),
        ("jawa_barat", "regency", "Kabupaten Bekasi", "kabupaten_bekasi", "32.16"),
        ("jawa_barat", "city", "Kota Depok", "depok", "32.76"),
        ("jawa_barat", "city", "Kota Cimahi", "cimahi", "32.77"),
        ("jawa_barat", "city", "Kota Sukabumi", "sukabumi", "32.72"),
        ("jawa_barat", "regency", "Kabupaten Sukabumi", "kabupaten_sukabumi", "32.02"),
        ("jawa_barat", "city", "Kota Cirebon", "cirebon", "32.74"),
        ("jawa_barat", "regency", "Kabupaten Cirebon", "kabupaten_cirebon", "32.09"),
        ("jawa_barat", "regency", "Kabupaten Garut", "garut", "32.05"),
        ("jawa_barat", "regency", "Kabupaten Karawang", "karawang", "32.15"),
        ("jawa_barat", "regency", "Kabupaten Indramayu", "indramayu", "32.12"),
        ("jawa_barat", "regency", "Kabupaten Subang", "subang", "32.13"),

        # Jawa Tengah
        ("jawa_tengah", "city", "Kota Semarang", "semarang", "33.74"),
        ("jawa_tengah", "regency", "Kabupaten Semarang", "kabupaten_semarang", "33.22"),
        ("jawa_tengah", "city", "Kota Surakarta", "surakarta", "33.72"),
        ("jawa_tengah", "city", "Kota Tegal", "tegal", "33.76"),
        ("jawa_tengah", "regency", "Kabupaten Tegal", "kabupaten_tegal", "33.28"),
        ("jawa_tengah", "city", "Kota Pekalongan", "pekalongan", "33.75"),
        ("jawa_tengah", "regency", "Kabupaten Pekalongan", "kabupaten_pekalongan", "33.26"),
        ("jawa_tengah", "regency", "Kabupaten Banyumas", "banyumas", "33.02"),
        ("jawa_tengah", "regency", "Kabupaten Cilacap", "cilacap", "33.01"),
        ("jawa_tengah", "regency", "Kabupaten Klaten", "klaten", "33.10"),
        ("jawa_tengah", "regency", "Kabupaten Kudus", "kudus", "33.19"),

        # DI Yogyakarta
        ("di_yogyakarta", "city", "Kota Yogyakarta", "yogyakarta", "34.71"),
        ("di_yogyakarta", "regency", "Kabupaten Sleman", "sleman", "34.04"),
        ("di_yogyakarta", "regency", "Kabupaten Bantul", "bantul", "34.02"),
        ("di_yogyakarta", "regency", "Kabupaten Gunungkidul", "gunungkidul", "34.03"),
        ("di_yogyakarta", "regency", "Kabupaten Kulon Progo", "kulon_progo", "34.01"),

        # Jawa Timur
        ("jawa_timur", "city", "Kota Surabaya", "surabaya", "35.78"),
        ("jawa_timur", "city", "Kota Malang", "malang", "35.73"),
        ("jawa_timur", "regency", "Kabupaten Malang", "kabupaten_malang", "35.07"),
        ("jawa_timur", "city", "Kota Kediri", "kediri", "35.71"),
        ("jawa_timur", "regency", "Kabupaten Kediri", "kabupaten_kediri", "35.06"),
        ("jawa_timur", "city", "Kota Madiun", "madiun", "35.77"),
        ("jawa_timur", "regency", "Kabupaten Madiun", "kabupaten_madiun", "35.19"),
        ("jawa_timur", "city", "Kota Probolinggo", "probolinggo", "35.74"),
        ("jawa_timur", "regency", "Kabupaten Probolinggo", "kabupaten_probolinggo", "35.13"),
        ("jawa_timur", "regency", "Kabupaten Sidoarjo", "sidoarjo", "35.15"),
        ("jawa_timur", "regency", "Kabupaten Jember", "jember", "35.09"),
        ("jawa_timur", "regency", "Kabupaten Banyuwangi", "banyuwangi", "35.10"),

        # Bali
        ("bali", "city", "Kota Denpasar", "denpasar", "51.71"),
        ("bali", "regency", "Kabupaten Badung", "badung", "51.03"),
        ("bali", "regency", "Kabupaten Gianyar", "gianyar", "51.04"),
        ("bali", "regency", "Kabupaten Buleleng", "buleleng", "51.08"),

        # Sumatera Utara
        ("sumatera_utara", "city", "Kota Medan", "medan", "12.71"),
        ("sumatera_utara", "city", "Kota Binjai", "binjai", "12.75"),
        ("sumatera_utara", "city", "Kota Pematangsiantar", "pematangsiantar", "12.72"),
        ("sumatera_utara", "regency", "Kabupaten Deli Serdang", "deli_serdang", "12.07"),

        # Sumatera Barat
        ("sumatera_barat", "city", "Kota Padang", "padang", "13.71"),
        ("sumatera_barat", "city", "Kota Bukittinggi", "bukittinggi", "13.75"),
        ("sumatera_barat", "regency", "Kabupaten Agam", "agam", "13.06"),

        # Sumatera Selatan
        ("sumatera_selatan", "city", "Kota Palembang", "palembang", "16.71"),
        ("sumatera_selatan", "city", "Kota Prabumulih", "prabumulih", "16.74"),
        ("sumatera_selatan", "regency", "Kabupaten Banyuasin", "banyuasin", "16.07"),

        # Riau
        ("riau", "city", "Kota Pekanbaru", "pekanbaru", "14.71"),
        ("riau", "city", "Kota Dumai", "dumai", "14.72"),
        ("riau", "regency", "Kabupaten Kampar", "kampar", "14.01"),

        # Lampung
        ("lampung", "city", "Kota Bandar Lampung", "bandar_lampung", "18.71"),
        ("lampung", "city", "Kota Metro", "metro", "18.72"),
        ("lampung", "regency", "Kabupaten Lampung Selatan", "lampung_selatan", "18.01"),

        # Kalimantan Selatan
        ("kalimantan_selatan", "city", "Kota Banjarmasin", "banjarmasin", "63.71"),
        ("kalimantan_selatan", "city", "Kota Banjarbaru", "banjarbaru", "63.72"),
        ("kalimantan_selatan", "regency", "Kabupaten Banjar", "banjar", "63.03"),

        # Kalimantan Timur
        ("kalimantan_timur", "city", "Kota Balikpapan", "balikpapan", "64.71"),
        ("kalimantan_timur", "city", "Kota Samarinda", "samarinda", "64.72"),
        ("kalimantan_timur", "city", "Kota Bontang", "bontang", "64.74"),

        # Kalimantan Barat
        ("kalimantan_barat", "city", "Kota Pontianak", "pontianak", "61.71"),
        ("kalimantan_barat", "city", "Kota Singkawang", "singkawang", "61.72"),

        # Sulawesi Selatan
        ("sulawesi_selatan", "city", "Kota Makassar", "makassar", "73.71"),
        ("sulawesi_selatan", "city", "Kota Parepare", "parepare", "73.72"),
        ("sulawesi_selatan", "regency", "Kabupaten Gowa", "gowa", "73.06"),

        # Sulawesi Utara
        ("sulawesi_utara", "city", "Kota Manado", "manado", "71.71"),
        ("sulawesi_utara", "city", "Kota Bitung", "bitung", "71.72"),
        ("sulawesi_utara", "city", "Kota Tomohon", "tomohon", "71.73"),

        # Nusa Tenggara Barat
        ("nusa_tenggara_barat", "city", "Kota Mataram", "mataram", "52.71"),
        ("nusa_tenggara_barat", "regency", "Kabupaten Lombok Barat", "lombok_barat", "52.01"),
        ("nusa_tenggara_barat", "regency", "Kabupaten Lombok Timur", "lombok_timur", "52.03"),

        # Nusa Tenggara Timur
        ("nusa_tenggara_timur", "city", "Kota Kupang", "kupang", "53.71"),
        ("nusa_tenggara_timur", "regency", "Kabupaten Kupang", "kabupaten_kupang", "53.01"),

        # Papua
        ("papua", "city", "Kota Jayapura", "jayapura", "91.71"),
        ("papua", "regency", "Kabupaten Jayapura", "kabupaten_jayapura", "91.03"),

        # Papua Barat
        ("papua_barat", "regency", "Kabupaten Manokwari", "manokwari", "92.02"),

        # Papua Barat Daya
        ("papua_barat_daya", "city", "Kota Sorong", "sorong", "96.71"),

        # Maluku
        ("maluku", "city", "Kota Ambon", "ambon", "81.71"),

        # Maluku Utara
        ("maluku_utara", "city", "Kota Ternate", "ternate", "82.71"),
        ("maluku_utara", "city", "Kota Tidore Kepulauan", "tidore_kepulauan", "82.72"),
    ]

    for province_code, level, display_name, normalized_name, city_code in city_regencies:
        parent = Location.objects.filter(
            level="province",
            province_code=province_code,
            is_active=True,
            is_false_positive=False,
        ).first()

        if not parent:
            continue

        Location.objects.update_or_create(
            level=level,
            city_regency_code=city_code,
            defaults={
                "name": display_name,
                "display_name": display_name,
                "normalized_name": normalized_name,
                "province_code": province_code,
                "parent": parent,
                "is_active": True,
                "is_false_positive": False,
            },
        )

    # =========================================================
    # 2. Minimal Province Aliases
    # =========================================================
    province_aliases = {
        "aceh": ["NAD", "Aceh"],
        "dki_jakarta": ["Jakarta", "DKI", "DKI Jakarta", "Ibukota", "Ibu Kota"],
        "di_yogyakarta": ["Yogyakarta", "Jogja", "Yogya", "DIY", "D.I. Yogyakarta"],
        "jawa_barat": ["Jabar", "Jawa Barat"],
        "jawa_tengah": ["Jateng", "Jawa Tengah"],
        "jawa_timur": ["Jatim", "Jawa Timur"],
        "sumatera_utara": ["Sumut", "Sumatera Utara"],
        "sumatera_barat": ["Sumbar", "Sumatera Barat"],
        "sumatera_selatan": ["Sumsel", "Sumatera Selatan"],
        "kalimantan_barat": ["Kalbar", "Kalimantan Barat"],
        "kalimantan_tengah": ["Kalteng", "Kalimantan Tengah"],
        "kalimantan_selatan": ["Kalsel", "Kalimantan Selatan"],
        "kalimantan_timur": ["Kaltim", "Kalimantan Timur"],
        "kalimantan_utara": ["Kaltara", "Kalimantan Utara"],
        "sulawesi_utara": ["Sulut", "Sulawesi Utara"],
        "sulawesi_tengah": ["Sulteng", "Sulawesi Tengah"],
        "sulawesi_selatan": ["Sulsel", "Sulawesi Selatan"],
        "sulawesi_tenggara": ["Sultra", "Sulawesi Tenggara"],
        "sulawesi_barat": ["Sulbar", "Sulawesi Barat"],
        "nusa_tenggara_barat": ["NTB", "Nusa Tenggara Barat"],
        "nusa_tenggara_timur": ["NTT", "Nusa Tenggara Timur"],
        "kepulauan_riau": ["Kepri", "Kepulauan Riau"],
        "kepulauan_bangka_belitung": ["Babel", "Bangka Belitung"],
        "papua_barat_daya": ["Papua Barat Daya", "PBD"],
    }

    for province_code, alias_list in province_aliases.items():
        location = Location.objects.filter(
            level="province",
            province_code=province_code,
            is_active=True,
            is_false_positive=False,
        ).first()

        if not location:
            continue

        for alias in alias_list:
            LocationAlias.objects.update_or_create(
                location=location,
                alias=alias,
                defaults={
                    "normalized_alias": alias.lower().strip(),
                    "is_active": True,
                },
            )

    # =========================================================
    # 2B. Minimal City/Regency Aliases
    # =========================================================
    city_aliases = {
        # Banten
        "Kota Cilegon": ["Cilegon", "Pemkot Cilegon", "Dinkes Cilegon", "Dinas Kesehatan Cilegon"],
        "Kota Serang": ["Serang", "Pemkot Serang", "Dinkes Serang", "Dinas Kesehatan Serang"],
        "Kota Tangerang": ["Tangerang", "Pemkot Tangerang", "Dinkes Tangerang"],
        "Kota Tangerang Selatan": ["Tangsel", "Tangerang Selatan", "Pemkot Tangsel", "Dinkes Tangsel"],
        "Kabupaten Serang": ["Kabupaten Serang", "Pemkab Serang", "Dinkes Kabupaten Serang"],
        "Kabupaten Tangerang": ["Kabupaten Tangerang", "Pemkab Tangerang", "Dinkes Kabupaten Tangerang"],
        "Kabupaten Pandeglang": ["Pandeglang", "Pemkab Pandeglang", "Dinkes Pandeglang"],
        "Kabupaten Lebak": ["Lebak", "Pemkab Lebak", "Dinkes Lebak"],

        # DKI
        "Jakarta Selatan": ["Jaksel", "Jakarta Selatan", "Dinkes Jakarta Selatan"],
        "Jakarta Timur": ["Jaktim", "Jakarta Timur", "Dinkes Jakarta Timur"],
        "Jakarta Pusat": ["Jakpus", "Jakarta Pusat", "Dinkes Jakarta Pusat"],
        "Jakarta Barat": ["Jakbar", "Jakarta Barat", "Dinkes Jakarta Barat"],
        "Jakarta Utara": ["Jakut", "Jakarta Utara", "Dinkes Jakarta Utara"],
        "Kepulauan Seribu": ["Kepulauan Seribu", "Pulau Seribu"],

        # Jawa Barat
        "Kota Bandung": ["Bandung", "Kota Bandung", "Pemkot Bandung", "Dinkes Bandung"],
        "Kabupaten Bandung": ["Kabupaten Bandung", "Pemkab Bandung", "Dinkes Kabupaten Bandung"],
        "Kabupaten Bandung Barat": ["Bandung Barat", "KBB", "Kabupaten Bandung Barat"],
        "Kota Bogor": ["Kota Bogor", "Pemkot Bogor", "Dinkes Kota Bogor"],
        "Kabupaten Bogor": ["Kabupaten Bogor", "Pemkab Bogor", "Dinkes Kabupaten Bogor"],
        "Kota Bekasi": ["Kota Bekasi", "Pemkot Bekasi", "Dinkes Kota Bekasi"],
        "Kabupaten Bekasi": ["Kabupaten Bekasi", "Pemkab Bekasi", "Dinkes Kabupaten Bekasi"],
        "Kota Depok": ["Depok", "Kota Depok", "Pemkot Depok", "Dinkes Depok"],
        "Kota Cimahi": ["Cimahi", "Pemkot Cimahi", "Dinkes Cimahi"],
        "Kota Sukabumi": ["Kota Sukabumi", "Pemkot Sukabumi"],
        "Kabupaten Sukabumi": ["Kabupaten Sukabumi", "Pemkab Sukabumi"],
        "Kota Cirebon": ["Kota Cirebon", "Pemkot Cirebon"],
        "Kabupaten Cirebon": ["Kabupaten Cirebon", "Pemkab Cirebon"],
        "Kabupaten Garut": ["Garut", "Pemkab Garut", "Dinkes Garut"],
        "Kabupaten Karawang": ["Karawang", "Pemkab Karawang", "Dinkes Karawang"],
        "Kabupaten Indramayu": ["Indramayu", "Pemkab Indramayu", "Dinkes Indramayu"],
        "Kabupaten Subang": ["Subang", "Pemkab Subang", "Dinkes Subang"],

        # Jawa Tengah
        "Kota Semarang": ["Kota Semarang", "Pemkot Semarang", "Dinkes Kota Semarang"],
        "Kabupaten Semarang": ["Kabupaten Semarang", "Pemkab Semarang"],
        "Kota Surakarta": ["Surakarta", "Solo", "Kota Solo", "Pemkot Solo"],
        "Kota Tegal": ["Kota Tegal", "Pemkot Tegal"],
        "Kabupaten Tegal": ["Kabupaten Tegal", "Pemkab Tegal"],
        "Kota Pekalongan": ["Kota Pekalongan", "Pemkot Pekalongan"],
        "Kabupaten Pekalongan": ["Kabupaten Pekalongan", "Pemkab Pekalongan"],
        "Kabupaten Banyumas": ["Banyumas", "Pemkab Banyumas"],
        "Kabupaten Cilacap": ["Cilacap", "Pemkab Cilacap"],
        "Kabupaten Klaten": ["Klaten", "Pemkab Klaten"],
        "Kabupaten Kudus": ["Kudus", "Pemkab Kudus"],

        # DIY
        "Kota Yogyakarta": ["Yogyakarta", "Jogja", "Yogya", "Dinkes Yogyakarta", "Dinkes Kota Yogyakarta"],
        "Kabupaten Sleman": ["Sleman", "Dinkes Sleman", "Pemkab Sleman"],
        "Kabupaten Bantul": ["Bantul", "Dinkes Bantul", "Pemkab Bantul"],
        "Kabupaten Gunungkidul": ["Gunungkidul", "Gunung Kidul", "Dinkes Gunungkidul"],
        "Kabupaten Kulon Progo": ["Kulon Progo", "Kulonprogo", "Dinkes Kulon Progo"],

        # Jawa Timur
        "Kota Surabaya": ["Surabaya", "Pemkot Surabaya", "Dinkes Surabaya"],
        "Kota Malang": ["Kota Malang", "Dinkes Kota Malang", "Pemkot Malang"],
        "Kabupaten Malang": ["Kabupaten Malang", "Dinkes Kabupaten Malang", "Pemkab Malang"],
        "Kota Kediri": ["Kota Kediri", "Pemkot Kediri"],
        "Kabupaten Kediri": ["Kabupaten Kediri", "Pemkab Kediri"],
        "Kota Madiun": ["Kota Madiun", "Pemkot Madiun"],
        "Kabupaten Madiun": ["Kabupaten Madiun", "Pemkab Madiun"],
        "Kota Probolinggo": ["Kota Probolinggo", "Pemkot Probolinggo"],
        "Kabupaten Probolinggo": ["Kabupaten Probolinggo", "Pemkab Probolinggo"],
        "Kabupaten Sidoarjo": ["Sidoarjo", "Pemkab Sidoarjo", "Dinkes Sidoarjo"],
        "Kabupaten Jember": ["Jember", "Pemkab Jember", "Dinkes Jember"],
        "Kabupaten Banyuwangi": ["Banyuwangi", "Pemkab Banyuwangi", "Dinkes Banyuwangi"],

        # Bali
        "Kota Denpasar": ["Denpasar", "Pemkot Denpasar", "Dinkes Denpasar"],
        "Kabupaten Badung": ["Badung", "Pemkab Badung", "Dinkes Badung"],
        "Kabupaten Gianyar": ["Gianyar", "Pemkab Gianyar", "Dinkes Gianyar"],
        "Kabupaten Buleleng": ["Buleleng", "Pemkab Buleleng", "Dinkes Buleleng"],

        # Sumatera
        "Kota Medan": ["Medan", "Pemkot Medan", "Dinkes Medan"],
        "Kota Binjai": ["Binjai", "Pemkot Binjai"],
        "Kota Pematangsiantar": ["Pematangsiantar", "Pematang Siantar", "Siantar"],
        "Kabupaten Deli Serdang": ["Deli Serdang", "Deliserdang"],
        "Kota Padang": ["Padang", "Pemkot Padang", "Dinkes Padang"],
        "Kota Bukittinggi": ["Bukittinggi", "Bukit Tinggi"],
        "Kabupaten Agam": ["Agam", "Pemkab Agam"],
        "Kota Palembang": ["Palembang", "Pemkot Palembang", "Dinkes Palembang"],
        "Kota Prabumulih": ["Prabumulih", "Pemkot Prabumulih"],
        "Kabupaten Banyuasin": ["Banyuasin", "Pemkab Banyuasin"],
        "Kota Pekanbaru": ["Pekanbaru", "Pemkot Pekanbaru", "Dinkes Pekanbaru"],
        "Kota Dumai": ["Dumai", "Pemkot Dumai"],
        "Kabupaten Kampar": ["Kampar", "Pemkab Kampar"],
        "Kota Bandar Lampung": ["Bandar Lampung", "Bandarlampung", "Dinkes Bandar Lampung"],
        "Kota Metro": ["Metro", "Kota Metro"],
        "Kabupaten Lampung Selatan": ["Lampung Selatan", "Lamsel"],

        # Kalimantan
        "Kota Banjarmasin": ["Banjarmasin", "Pemkot Banjarmasin", "Dinkes Banjarmasin"],
        "Kota Banjarbaru": ["Banjarbaru", "Pemkot Banjarbaru", "Dinkes Banjarbaru"],
        "Kabupaten Banjar": ["Kabupaten Banjar", "Pemkab Banjar"],
        "Kota Balikpapan": ["Balikpapan", "Pemkot Balikpapan", "Dinkes Balikpapan"],
        "Kota Samarinda": ["Samarinda", "Pemkot Samarinda", "Dinkes Samarinda"],
        "Kota Bontang": ["Bontang", "Pemkot Bontang"],
        "Kota Pontianak": ["Pontianak", "Pemkot Pontianak", "Dinkes Pontianak"],
        "Kota Singkawang": ["Singkawang", "Pemkot Singkawang"],

        # Sulawesi
        "Kota Makassar": ["Makassar", "Pemkot Makassar", "Dinkes Makassar"],
        "Kota Parepare": ["Parepare", "Pare-pare", "Pemkot Parepare"],
        "Kabupaten Gowa": ["Gowa", "Pemkab Gowa"],
        "Kota Manado": ["Manado", "Pemkot Manado", "Dinkes Manado"],
        "Kota Bitung": ["Bitung", "Pemkot Bitung"],
        "Kota Tomohon": ["Tomohon", "Pemkot Tomohon"],

        # NTB / NTT
        "Kota Mataram": ["Mataram", "Pemkot Mataram", "Dinkes Mataram"],
        "Kabupaten Lombok Barat": ["Lombok Barat", "Lobar"],
        "Kabupaten Lombok Timur": ["Lombok Timur", "Lotim"],
        "Kota Kupang": ["Kota Kupang", "Pemkot Kupang", "Dinkes Kota Kupang"],
        "Kabupaten Kupang": ["Kabupaten Kupang", "Pemkab Kupang"],

        # Papua / Maluku
        "Kota Jayapura": ["Kota Jayapura", "Pemkot Jayapura", "Dinkes Jayapura"],
        "Kabupaten Jayapura": ["Kabupaten Jayapura", "Pemkab Jayapura"],
        "Kabupaten Manokwari": ["Manokwari", "Pemkab Manokwari"],
        "Kota Sorong": ["Sorong", "Kota Sorong", "Pemkot Sorong"],
        "Kota Ambon": ["Ambon", "Pemkot Ambon", "Dinkes Ambon"],
        "Kota Ternate": ["Ternate", "Pemkot Ternate", "Dinkes Ternate"],
        "Kota Tidore Kepulauan": ["Tidore", "Tidore Kepulauan", "Kota Tidore"],
    }

    for location_name, alias_list in city_aliases.items():
        location = Location.objects.filter(
            display_name=location_name,
            is_active=True,
            is_false_positive=False,
        ).first()

        if not location:
            continue

        for alias in alias_list:
            LocationAlias.objects.update_or_create(
                location=location,
                alias=alias,
                defaults={
                    "normalized_alias": alias.lower().strip(),
                    "is_active": True,
                },
            )

    # =========================================================
    # 3. Minimal Scoring Rules
    # =========================================================
    rules = [
        ("KLB", "klb", 40),
        ("Kejadian Luar Biasa", "kejadian luar biasa", 40),
        ("Wabah", "wabah", 35),
        ("Darurat", "darurat", 30),
        ("Waspada", "waspada", 20),
        ("Meninggal", "meninggal", 30),
        ("Kematian", "kematian", 30),
        ("Tewas", "tewas", 30),
        ("Fatal", "fatal", 25),
        ("Dirawat", "dirawat", 20),
        ("Rawat Inap", "rawat inap", 20),
        ("Kasus meningkat", "meningkat", 20),
        ("Lonjakan kasus", "lonjakan", 25),
        ("Penyebaran", "penyebaran", 20),
        ("Merebak", "merebak", 25),
        ("Meluas", "meluas", 25),
        ("Suspek", "suspek", 15),
        ("Positif", "positif", 15),
        ("Terdeteksi", "terdeteksi", 15),
        ("Ditemukan", "ditemukan", 10),
        ("Dilaporkan", "dilaporkan", 10),

        # Penyakit
        ("DBD", "dbd", 25),
        ("Demam Berdarah", "demam berdarah", 25),
        ("Dengue", "dengue", 25),
        ("Mpox", "mpox", 30),
        ("Monkeypox", "monkeypox", 30),
        ("Flu Burung", "flu burung", 35),
        ("Avian Influenza", "avian influenza", 35),
        ("Antraks", "antraks", 35),
        ("Anthrax", "anthrax", 35),
        ("Rabies", "rabies", 30),
        ("Polio", "polio", 35),
        ("Difteri", "difteri", 30),
        ("Campak", "campak", 25),
        ("Rubela", "rubela", 20),
        ("Diare", "diare", 15),
        ("Kolera", "kolera", 35),
        ("Demam Tifoid", "demam tifoid", 20),
        ("Tifoid", "tifoid", 20),
        ("Chikungunya", "chikungunya", 25),
        ("Leptospirosis", "leptospirosis", 30),
        ("TBC", "tbc", 20),
        ("Tuberkulosis", "tuberkulosis", 20),
        ("HIV", "hiv", 20),
        ("AIDS", "aids", 20),
        ("IMS", "ims", 15),
        ("Infeksi Menular Seksual", "infeksi menular seksual", 15),
        ("Pertusis", "pertusis", 25),
        ("Batuk Rejan", "batuk rejan", 25),
        ("Nipah", "nipah", 40),
    ]

    for name, keyword, weight in rules:
        ScoringRule.objects.update_or_create(
            name=name,
            defaults={
                "keyword": keyword,
                "weight": weight,
                "is_active": True,
            },
        )