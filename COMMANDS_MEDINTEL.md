# Kompilasi Command Medintel

Jalankan semua command dari folder `backend`:

```bash
cd backend
```

## Rekomendasi Mulai Dari Data Bersih

Gunakan ini kalau database lama sudah berantakan tetapi master data masih mau dipertahankan:

```bash
python manage.py migrate
python manage.py reset_operational_data --yes
python manage.py seed_roles
python manage.py seed_disease_master --sync-signals
python manage.py seed_publisher_aliases
python manage.py import_province_geojson --file data/geo/indonesia_provinces.geojson --update-existing
python manage.py import_kabkota_geojson --file data/geo/indonesia_kabkota.geojson --update-existing --create-missing-province
python manage.py generate_location_aliases
python manage.py deduplicate_provinces --dry-run
python manage.py run_legacy_crawler
python manage.py triage_signals
python manage.py build_signal_clusters --reset
python manage.py compact_signal_storage --dry-run
```

Jika hasil `deduplicate_provinces --dry-run` sudah aman:

```bash
python manage.py deduplicate_provinces
```

Jika hasil `compact_signal_storage --dry-run` sudah aman:

```bash
python manage.py compact_signal_storage --content-days 14
```

## Command Operasional Utama

| Command | Fungsi | Contoh |
|---|---|---|
| `run_legacy_crawler` | Crawling Google News berbasis Disease Master dan ingest ke Signal | `python manage.py run_legacy_crawler` |
| `triage_signals` | Mengisi triage priority, confidence, dan rekomendasi approval | `python manage.py triage_signals` |
| `build_signal_clusters` | Membentuk cluster/unique health event dari signal non-noise | `python manage.py build_signal_clusters --reset` |
| `compact_signal_storage` | Memangkas teks panjang dan opsional hapus noise/raw lama | `python manage.py compact_signal_storage --dry-run` |

## Command Seed dan Reference Data

| Command | Fungsi | Contoh |
|---|---|---|
| `seed_roles` | Membuat grup role default | `python manage.py seed_roles` |
| `seed_disease_master` | Mengisi Disease Master SKDR, emerging, re-emerging | `python manage.py seed_disease_master --sync-signals` |
| `seed_publisher_aliases` | Mengisi alias domain publisher untuk URL resolver | `python manage.py seed_publisher_aliases` |
| `import_province_geojson` | Import/update provinsi dari GeoJSON | `python manage.py import_province_geojson --file data/geo/indonesia_provinces.geojson --update-existing` |
| `import_kabkota_geojson` | Import/update kab/kota dari GeoJSON | `python manage.py import_kabkota_geojson --file data/geo/indonesia_kabkota.geojson --update-existing --create-missing-province` |
| `generate_location_aliases` | Generate alias lokasi dari master Location | `python manage.py generate_location_aliases` |
| `deduplicate_provinces` | Membersihkan duplikasi provinsi | `python manage.py deduplicate_provinces --dry-run` |

## Command Backfill

| Command | Fungsi | Contoh |
|---|---|---|
| `backfill_signal_raw_locations` | Mengisi `raw_location_text` dari judul/konten signal lama | `python manage.py backfill_signal_raw_locations --only-empty --dry-run` |
| `backfill_signal_locations` | Membentuk `SignalLocation` dari `raw_location_text` | `python manage.py backfill_signal_locations --only-empty` |
| `import_signals` | Import CSV crawling lama | `python manage.py import_signals --file ../data/raw/data_intel_raw.csv` |

## Command Reset dan Maintenance

| Command | Risiko | Fungsi | Contoh |
|---|---|---|---|
| `reset_operational_data` | Destructive, tetapi aman untuk master data | Hapus Signal, SignalLocation, SignalCluster, Alert, AuditLog, URL cache, dan Source | `python manage.py reset_operational_data --yes` |
| `reset_operational_data --keep-sources` | Destructive terbatas | Sama seperti di atas, tetapi Source dipertahankan | `python manage.py reset_operational_data --yes --keep-sources` |
| `reset_intel_all` | Sangat destructive | Hapus hampir semua data app intel lama, termasuk Location/ScoringRule/SystemSetting | `python manage.py reset_intel_all --yes` |
| `compact_signal_storage --delete-noise` | Menghapus noise lama | Hapus noise setelah umur tertentu | `python manage.py compact_signal_storage --delete-noise --noise-days 30` |
| `compact_signal_storage --delete-raw` | Menghapus raw lama | Hapus raw belum diproses setelah umur tertentu | `python manage.py compact_signal_storage --delete-raw --raw-days 90` |
| `compact_signal_storage --vacuum` | Aman untuk SQLite, perlu waktu | Mengecilkan file SQLite setelah penghapusan | `python manage.py compact_signal_storage --vacuum` |

## Command Lama / Hindari Jika Tidak Perlu

| Command | Catatan |
|---|---|
| `normalize_location_codes(DEPTRECATED)` | Ditandai deprecated, hindari kecuali benar-benar tahu efeknya |
| `reset_intel_all` | Terlalu luas untuk kasus bersih-bersih data crawling; lebih baik pakai `reset_operational_data` |

