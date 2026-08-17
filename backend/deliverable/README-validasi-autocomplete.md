# Perbaikan Validasi Artikel dan Autocomplete Lokasi

## Perubahan

- pilihan lokasi utama ditingkatkan menjadi autocomplete yang dapat dicari;
- negara yang belum tersedia dapat ditambahkan langsung oleh validator;
- negara baru otomatis dikaitkan ke artikel dan ditetapkan sebagai lokasi utama;
- select asli tetap tersedia sebagai fallback ketika JavaScript tidak aktif;
- tombol **Simpan Status Validasi** memakai aksi tersendiri;
- penyimpanan status tidak lagi terhambat validasi field Neraca Informasi;
- alasan penolakan tetap diwajibkan untuk status **Tidak Relevan**;
- pesan keberhasilan dan riwayat perubahan status menggunakan catatan validasi;
- test regresi ditambahkan untuk submit status dan render autocomplete.
- test regresi ditambahkan untuk penambahan negara luar negeri.
- test halaman validasi memakai static storage non-manifest khusus pengujian,
  sehingga tidak bergantung pada hasil `collectstatic` production.
- penambahan lokasi baru memakai dialog responsif yang tidak terpotong panel,
  serta mendukung negara, provinsi/negara bagian, kota, dan wilayah setara.

## File yang diperbarui

- `apps/assessments/forms.py`
- `apps/dashboard/views.py`
- `apps/dashboard/tests_article_validation_queue.py`
- `templates/dashboard/article_validation.html`
- `templates/base.html`
- `static/css/medintel.css`

## Pemasangan

Ekstrak paket ke root folder `backend` dengan mempertahankan struktur folder,
kemudian jalankan:

```powershell
python manage.py check
python manage.py test apps.dashboard.tests_article_validation_queue
python manage.py collectstatic --noinput
```

Tidak ada perubahan model atau migration baru.

Setelah push ke GitHub, deploy ulang service web Railway. Worker tidak perlu
command baru; worker hanya perlu ikut redeploy bila Railway mengaturnya otomatis
dari repository yang sama.
