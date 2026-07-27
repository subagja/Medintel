import json
import re
import unicodedata
from pathlib import Path

import geopandas as gpd
from shapely.validation import make_valid


def normalize_region_code(value: str) -> str:
    if not value:
        return ""
    value = str(value).strip().lower()
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.replace("/", " ")
    value = re.sub(r"[^a-z0-9\s_-]", "", value)
    value = re.sub(r"[\s-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def normalize_kabkota_name(value: str) -> str:
    if not value:
        return ""
    value = str(value).strip()
    value = re.sub(r"\s+", " ", value)

    value = re.sub(r"^kab\.\s*", "Kabupaten ", value, flags=re.IGNORECASE)
    value = re.sub(r"^kabupaten\s*", "Kabupaten ", value, flags=re.IGNORECASE)
    value = re.sub(r"^kota\s*", "Kota ", value, flags=re.IGNORECASE)

    return value.strip()


def infer_level(kabkota_name: str) -> str:
    lower = (kabkota_name or "").strip().lower()
    if lower.startswith("kota "):
        return "city"
    return "regency"


INPUT_FILE = Path(r"backend/intel/static/geo/indonesia_kabkota_source.geojson")
OUTPUT_FILE = Path(r"backend/intel/static/geo/indonesia_kabkota.geojson")


def safe_fix_geometry(geom):
    if geom is None or geom.is_empty:
        return None

    try:
        if geom.is_valid:
            return geom
    except Exception:
        pass

    # First attempt: make_valid
    try:
        fixed = make_valid(geom)
        if fixed is not None and not fixed.is_empty:
            return fixed
    except Exception:
        pass

    # Fallback: buffer(0)
    try:
        fixed = geom.buffer(0)
        if fixed is not None and not fixed.is_empty:
            return fixed
    except Exception:
        pass

    return None


def main():
    print(f"Reading source: {INPUT_FILE}")
    gdf = gpd.read_file(INPUT_FILE)

    required_cols = ["WADMKK", "WADMPR", "KDPKAB"]
    for col in required_cols:
        if col not in gdf.columns:
            raise ValueError(f"Required column '{col}' not found in source file.")

    # remove null geometry
    before_count = len(gdf)
    gdf = gdf[gdf.geometry.notnull()].copy()
    print(f"Rows with non-null geometry: {len(gdf)} / {before_count}")

    # fix invalid geometries
    gdf["geometry"] = gdf["geometry"].apply(safe_fix_geometry)

    # drop rows still broken after repair
    after_fix_count = len(gdf)
    gdf = gdf[gdf.geometry.notnull()].copy()
    gdf = gdf[~gdf.geometry.is_empty].copy()
    print(f"Rows after geometry fix: {len(gdf)} / {after_fix_count}")

    # optional: keep only polygon-like geometries
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    print(f"Rows after polygon filter: {len(gdf)}")

    gdf["kabkota_name"] = gdf["WADMKK"].fillna("").astype(str).map(normalize_kabkota_name)
    gdf["province_name"] = gdf["WADMPR"].fillna("").astype(str).str.strip()
    gdf["bps_code"] = gdf["KDPKAB"].fillna("").astype(str).str.strip()

    gdf["city_regency_code"] = gdf["kabkota_name"].map(normalize_region_code)
    gdf["province_code"] = gdf["province_name"].map(normalize_region_code)
    gdf["level"] = gdf["kabkota_name"].map(infer_level)

    gdf = gdf[
        (gdf["kabkota_name"] != "") &
        (gdf["province_name"] != "") &
        (gdf["city_regency_code"] != "")
    ].copy()

    print(f"Rows ready for dissolve: {len(gdf)}")

    # dissolve
    dissolved = gdf.dissolve(
        by=[
            "kabkota_name",
            "city_regency_code",
            "province_name",
            "province_code",
            "bps_code",
            "level",
        ],
        as_index=False
    )

    # fix again after dissolve, just in case
    dissolved["geometry"] = dissolved["geometry"].apply(safe_fix_geometry)
    dissolved = dissolved[dissolved.geometry.notnull()].copy()
    dissolved = dissolved[~dissolved.geometry.is_empty].copy()

    features = []

    for _, row in dissolved.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        geom_json = json.loads(
            gpd.GeoSeries([geom], crs=dissolved.crs).to_json()
        )["features"][0]["geometry"]

        feature = {
            "type": "Feature",
            "properties": {
                "name": row["kabkota_name"],
                "city_regency_code": row["city_regency_code"],
                "display_name": row["kabkota_name"],
                "level": row["level"],
                "country_code": "ID",
                "bps_code": row["bps_code"],
                "province_name": row["province_name"],
                "province_code": row["province_code"],
                "source": "user_uploaded_base_geojson_dissolved",
            },
            "geometry": geom_json,
        }
        features.append(feature)

    final_geojson = {
        "type": "FeatureCollection",
        "features": features,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(final_geojson, f, ensure_ascii=False)

    print(f"Saved {len(features)} dissolved kabupaten/kota features to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()