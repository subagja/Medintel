from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path


POPULATION_REFERENCE_YEAR = 2025
POPULATION_SOURCE_NAME = "Badan Pusat Statistik"
POPULATION_SOURCE_URL = (
    "https://www.bps.go.id/id/statistics-table/3/"
    "V1ZSbFRUY3lTbFpEYTNsVWNGcDZjek53YkhsNFFUMDkjMyMwMDAw/"
    "jumlah-penduduk--laju-pertumbuhan-penduduk--distribusi-"
    "persentase-penduduk--kepadatan-penduduk--rasio-jenis-"
    "kelamin-penduduk-menurut-provinsi.html?year=2025"
)
ROLLING_WINDOW_DAYS = 14
MAP_MODE_CUMULATIVE = "cumulative"
MAP_MODE_ROLLING = "rolling"


@lru_cache(maxsize=1)
def province_population_reference() -> dict[str, dict]:
    """Load the versioned BPS province denominator bundled with the app."""
    csv_path = (
        Path(__file__).resolve().parent
        / "data"
        / "bps_province_population_2025.csv"
    )
    reference = {}
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            code = "".join(character for character in row["province_code"] if character.isdigit())[:2]
            reference[code] = {
                "province_code": code,
                "province_name": row["province_name"],
                "population": int(row["population"]),
                "reference_year": int(row["reference_year"]),
                "source_name": row["source_name"],
                "source_url": row["source_url"],
            }
    return reference


def normalize_bps_code(value: str | None) -> str:
    return "".join(character for character in (value or "") if character.isdigit())


def province_population(location_code: str | None) -> dict | None:
    code = normalize_bps_code(location_code)[:2]
    if len(code) != 2:
        return None
    return province_population_reference().get(code)


def rate_per_100k(case_count: int, population: int | None) -> float | None:
    if not population or population <= 0:
        return None
    return round((case_count / population) * 100_000, 4)


def metric_metadata(
    level: str,
    mode: str = MAP_MODE_ROLLING,
) -> dict:
    if mode == MAP_MODE_CUMULATIVE:
        return {
            "id": "osint_validated_cumulative_reported_cases",
            "label": (
                "Akumulasi Kasus Terlapor dari Artikel Tervalidasi"
            ),
            "short_label": "Akumulasi kasus terlapor",
            "unit": "kasus",
            "normalized": False,
            "reference_year": None,
            "source_name": "Artikel OSINT tervalidasi",
            "source_url": "",
            "window_days": None,
            "mode": MAP_MODE_CUMULATIVE,
            "caveat": (
                "Akumulasi berasal dari angka laporan unik pada artikel "
                "OSINT yang faktanya telah divalidasi/dikoreksi; bukan "
                "total kasus resmi dan tidak menyatakan KLB."
            ),
        }

    if level == "province":
        return {
            "id": "osint_reported_case_rate_per_100k",
            "label": "Rasio Kasus Terlapor OSINT per 100.000 Penduduk",
            "short_label": "Rasio per 100.000 penduduk",
            "unit": "per 100.000 penduduk",
            "normalized": True,
            "reference_year": POPULATION_REFERENCE_YEAR,
            "source_name": POPULATION_SOURCE_NAME,
            "source_url": POPULATION_SOURCE_URL,
            "window_days": ROLLING_WINDOW_DAYS,
            "mode": MAP_MODE_ROLLING,
            "caveat": (
                "Indikator berbasis kasus yang ditemukan dari artikel OSINT; "
                "bukan angka insidensi resmi dan tidak menyatakan KLB."
            ),
        }

    return {
        "id": "osint_reported_case_count",
        "label": "Jumlah Kasus Terlapor dari Artikel",
        "short_label": "Jumlah kasus terlapor",
        "unit": "kasus",
        "normalized": False,
        "reference_year": None,
        "source_name": "Artikel OSINT terhimpun",
        "source_url": "",
        "window_days": ROLLING_WINDOW_DAYS,
        "mode": MAP_MODE_ROLLING,
        "caveat": (
            "Belum dinormalisasi berdasarkan jumlah penduduk kabupaten/kota; "
            "warna menunjukkan volume laporan yang ditemukan, bukan tingkat risiko."
        ),
    }
