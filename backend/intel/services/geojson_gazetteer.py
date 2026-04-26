import json
import re
import unicodedata
from pathlib import Path

from django.conf import settings
from django.db import transaction

from intel.models import Location, LocationAlias


def normalize_region_code(value: str) -> str:
    if not value:
        return ""

    value = str(value).strip().lower()
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.replace("/", " ")
    value = re.sub(r"[^a-z0-9\s_-]", "", value)
    value = re.sub(r"[\s\-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def normalize_alias(value: str) -> str:
    if not value:
        return ""

    value = str(value).strip().lower()
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.replace("/", " ")
    value = re.sub(r"[^a-z0-9\s\-.]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def clean_name(value: str) -> str:
    return (value or "").strip()


def strip_admin_prefix(name: str) -> str:
    value = clean_name(name)

    value = re.sub(
        r"^(provinsi|prov\.|kabupaten|kab\.|kab|kota|kecamatan|kec\.|kelurahan|desa)\s+",
        "",
        value,
        flags=re.IGNORECASE,
    )

    return value.strip()


def infer_kabkota_level(display_name: str, level_from_geojson: str = "") -> str:
    level_from_geojson = (level_from_geojson or "").strip().lower()
    name = (display_name or "").strip().lower()

    if level_from_geojson in ["city", "kota"]:
        return "city"

    if level_from_geojson in ["regency", "kabupaten"]:
        return "regency"

    if name.startswith("kota "):
        return "city"

    return "regency"


def get_geojson_data_dir() -> Path:
    return Path(settings.BASE_DIR) / "data" / "geo"


def ensure_alias(location: Location, alias: str, is_primary: bool = False):
    alias = clean_name(alias)

    if not alias:
        return

    LocationAlias.objects.update_or_create(
        location=location,
        alias=alias,
        defaults={
            "normalized_alias": normalize_alias(alias),
            "is_active": True,
            "is_primary": is_primary,
        },
    )


def generate_province_aliases(location: Location):
    display_name = location.display_name or location.name
    short_name = strip_admin_prefix(display_name)

    ensure_alias(location, display_name, is_primary=True)
    ensure_alias(location, short_name)

    if short_name:
        ensure_alias(location, f"Provinsi {short_name}")
        ensure_alias(location, f"Pemprov {short_name}")
        ensure_alias(location, f"Dinkes {short_name}")
        ensure_alias(location, f"Dinas Kesehatan {short_name}")

    province_short_aliases = {
        "DKI Jakarta": ["Jakarta", "DKI", "Ibu Kota", "Ibukota"],
        "DI Yogyakarta": ["Yogyakarta", "DIY", "Jogja", "Yogya"],
        "Jawa Barat": ["Jabar"],
        "Jawa Tengah": ["Jateng"],
        "Jawa Timur": ["Jatim"],
        "Banten": ["Banten"],
        "Nusa Tenggara Barat": ["NTB"],
        "Nusa Tenggara Timur": ["NTT"],
        "Kalimantan Barat": ["Kalbar"],
        "Kalimantan Tengah": ["Kalteng"],
        "Kalimantan Selatan": ["Kalsel"],
        "Kalimantan Timur": ["Kaltim"],
        "Kalimantan Utara": ["Kaltara"],
        "Sulawesi Utara": ["Sulut"],
        "Sulawesi Tengah": ["Sulteng"],
        "Sulawesi Selatan": ["Sulsel"],
        "Sulawesi Tenggara": ["Sultra"],
        "Sulawesi Barat": ["Sulbar"],
        "Sumatera Utara": ["Sumut"],
        "Sumatera Barat": ["Sumbar"],
        "Sumatera Selatan": ["Sumsel"],
        "Kepulauan Riau": ["Kepri"],
        "Kepulauan Bangka Belitung": ["Babel", "Bangka Belitung"],
    }

    for alias in province_short_aliases.get(display_name, []):
        ensure_alias(location, alias)


def generate_kabkota_aliases(location: Location):
    display_name = location.display_name or location.name
    short_name = strip_admin_prefix(display_name)

    ensure_alias(location, display_name, is_primary=True)
    ensure_alias(location, short_name)

    if not short_name:
        return

    if location.level == "city":
        ensure_alias(location, f"Kota {short_name}")
        ensure_alias(location, f"Pemkot {short_name}")
        ensure_alias(location, f"Dinkes {short_name}")
        ensure_alias(location, f"Dinkes Kota {short_name}")
        ensure_alias(location, f"Dinas Kesehatan {short_name}")
        ensure_alias(location, f"Dinas Kesehatan Kota {short_name}")

    elif location.level == "regency":
        ensure_alias(location, f"Kabupaten {short_name}")
        ensure_alias(location, f"Kab. {short_name}")
        ensure_alias(location, f"Kab {short_name}")
        ensure_alias(location, f"Pemkab {short_name}")
        ensure_alias(location, f"Dinkes {short_name}")
        ensure_alias(location, f"Dinkes Kabupaten {short_name}")
        ensure_alias(location, f"Dinas Kesehatan {short_name}")
        ensure_alias(location, f"Dinas Kesehatan Kabupaten {short_name}")


def load_geojson(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def import_provinces_from_geojson(path: Path):
    data = load_geojson(path)
    created = 0
    updated = 0

    for feature in data.get("features", []):
        props = feature.get("properties", {}) or {}

        display_name = clean_name(
            props.get("display_name")
            or props.get("name")
            or props.get("province_name")
        )

        if not display_name:
            continue

        # Untuk konsistensi sistem, province_code kita buat dari display_name,
        # bukan mengambil mentah dari GeoJSON, karena ada beberapa property
        # province_code yang tampak tidak normal.
        province_code = normalize_region_code(display_name)

        location, was_created = Location.objects.update_or_create(
            level="province",
            province_code=province_code,
            defaults={
                "name": display_name,
                "display_name": display_name,
                "normalized_name": normalize_region_code(display_name),
                "country_code": props.get("country_code") or "ID",
                "city_regency_code": "",
                "parent": None,
                "is_active": True,
                "is_false_positive": False,
            },
        )

        generate_province_aliases(location)

        if was_created:
            created += 1
        else:
            updated += 1

    return {
        "created": created,
        "updated": updated,
        "total": created + updated,
    }


def find_province_for_kabkota(props: dict):
    province_name = clean_name(props.get("province_name") or "")
    province_code = clean_name(props.get("province_code") or "")

    province = None

    if province_name:
        province = Location.objects.filter(
            level="province",
            is_active=True,
            is_false_positive=False,
        ).filter(
            display_name__iexact=province_name
        ).first()

        if province:
            return province

        normalized_name = normalize_region_code(province_name)
        province = Location.objects.filter(
            level="province",
            province_code=normalized_name,
            is_active=True,
            is_false_positive=False,
        ).first()

        if province:
            return province

    if province_code:
        normalized_code = normalize_region_code(province_code)
        province = Location.objects.filter(
            level="province",
            province_code=normalized_code,
            is_active=True,
            is_false_positive=False,
        ).first()

        if province:
            return province

    return None


def import_kabkota_from_geojson(path: Path):
    data = load_geojson(path)
    created = 0
    updated = 0
    skipped_no_parent = 0

    for feature in data.get("features", []):
        props = feature.get("properties", {}) or {}

        display_name = clean_name(
            props.get("display_name")
            or props.get("name")
        )

        if not display_name:
            continue

        parent = find_province_for_kabkota(props)

        if not parent:
            skipped_no_parent += 1
            continue

        level = infer_kabkota_level(
            display_name=display_name,
            level_from_geojson=props.get("level") or "",
        )

        # city_regency_code harus mengikuti GeoJSON agar thematic kab/kota cocok.
        city_regency_code = clean_name(
            props.get("city_regency_code")
            or normalize_region_code(display_name)
        )

        normalized_name = normalize_region_code(strip_admin_prefix(display_name))

        location, was_created = Location.objects.update_or_create(
            level=level,
            city_regency_code=city_regency_code,
            province_code=parent.province_code,
            defaults={
                "name": display_name,
                "display_name": display_name,
                "normalized_name": normalized_name,
                "country_code": props.get("country_code") or "ID",
                "parent": parent,
                "is_active": True,
                "is_false_positive": False,
            },
        )

        generate_kabkota_aliases(location)

        if was_created:
            created += 1
        else:
            updated += 1

    return {
        "created": created,
        "updated": updated,
        "skipped_no_parent": skipped_no_parent,
        "total": created + updated,
    }


def ensure_geojson_gazetteer():
    """
    Dipanggil otomatis sebelum crawler berjalan.

    File yang dibaca:
    backend/data/geo/indonesia_provinces.geojson
    backend/data/geo/indonesia_kabkota.geojson
    """

    data_dir = get_geojson_data_dir()
    provinces_path = data_dir / "indonesia_provinces.geojson"
    kabkota_path = data_dir / "indonesia_kabkota.geojson"

    result = {
        "data_dir": str(data_dir),
        "provinces_file_exists": provinces_path.exists(),
        "kabkota_file_exists": kabkota_path.exists(),
        "provinces": {
            "created": 0,
            "updated": 0,
            "total": 0,
        },
        "kabkota": {
            "created": 0,
            "updated": 0,
            "skipped_no_parent": 0,
            "total": 0,
        },
        "aliases_total": 0,
    }

    with transaction.atomic():
        if provinces_path.exists():
            result["provinces"] = import_provinces_from_geojson(provinces_path)

        if kabkota_path.exists():
            result["kabkota"] = import_kabkota_from_geojson(kabkota_path)

        result["aliases_total"] = LocationAlias.objects.count()

    return result