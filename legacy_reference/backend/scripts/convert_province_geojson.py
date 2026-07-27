import json
import re
import unicodedata
from pathlib import Path


def normalize_region_code(value: str) -> str:
    if not value:
        return ""
    value = str(value).strip().lower()
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.replace("/", " ")
    value = re.sub(r"[^a-z0-9\\s_-]", "", value)
    value = re.sub(r"[\\s-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


INPUT_FILE = Path(r"backend/intel/static/geo/indonesia_provinces_source.geojson")
OUTPUT_FILE = Path(r"backend/intel/static/geo/indonesia_provinces.geojson")

with INPUT_FILE.open("r", encoding="utf-8") as f:
    data = json.load(f)

features_out = []

for feature in data.get("features", []):
    props = feature.get("properties", {}) or {}
    geometry = feature.get("geometry")

    province_name = (props.get("PROVINSI") or "").strip()
    bps_code = str(props.get("KODE_PROV") or "").strip()

    if not province_name or not geometry:
        continue

    province_code = normalize_region_code(province_name)

    new_feature = {
        "type": "Feature",
        "properties": {
            "name": province_name,
            "province_code": province_code,
            "display_name": province_name,
            "level": "province",
            "country_code": "ID",
            "bps_code": bps_code,
            "source": "user_uploaded_geojson",
        },
        "geometry": geometry,
    }

    features_out.append(new_feature)

final_geojson = {
    "type": "FeatureCollection",
    "features": features_out,
}

with OUTPUT_FILE.open("w", encoding="utf-8") as f:
    json.dump(final_geojson, f, ensure_ascii=False)

print(f"Saved {len(features_out)} features to {OUTPUT_FILE}")