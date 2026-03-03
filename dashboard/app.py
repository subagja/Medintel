import csv
import re
from datetime import datetime
from collections import Counter
from urllib.parse import urljoin

import requests
import streamlit as st
import folium
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

st.set_page_config(page_title="MedIntel-ID | OSINT System", layout="wide")
st.title("MedIntel-ID | OSINT System")

RAW_PATH = "../crawler/output/data_intel_raw.csv"
GEO_PATH = "../crawler/output/data_intel_geo.csv"
DEFAULT_API_BASE = "http://127.0.0.1:8000/api/"

# Indonesia bbox (anti outlier)
ID_LAT_MIN, ID_LAT_MAX = -11.5, 6.5
ID_LON_MIN, ID_LON_MAX = 95.0, 141.5


# =========================
# HELPERS
# =========================
def load_csv(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def parse_int(x, default=0):
    try:
        return int(str(x).strip() or default)
    except Exception:
        return default


def safe_float(x):
    try:
        if x is None:
            return None
        s = str(x).strip()
        if s in ["", "NA", "NaN", "None", "null"]:
            return None
        return float(s)
    except Exception:
        return None


def has_latlon(r):
    lat = safe_float(r.get("lat"))
    lon = safe_float(r.get("lon"))
    return (lat is not None) and (lon is not None)


def in_indonesia_bbox(lat, lon):
    return (ID_LAT_MIN <= lat <= ID_LAT_MAX) and (ID_LON_MIN <= lon <= ID_LON_MAX)


def clean_for_table(s):
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def parse_date_any(s):
    """
    Support ISO8601 from DRF: 2026-03-03T10:12:45Z / +07:00
    Also supports RFC-ish and simple formats.
    """
    if not s:
        return ""
    s = str(s).strip()

    # Fast path: find YYYY-MM-DD anywhere
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)

    fmts = [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    return ""


def api_get(api_base, path, params=None):
    url = urljoin(api_base, path)
    r = requests.get(url, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


def median(vals):
    vals = sorted(vals)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


# =========================
# API FETCHERS
# =========================
@st.cache_data(ttl=60)
def fetch_signals(api_base, min_score):
    page = 1
    page_size = 200
    all_rows = []

    while True:
        data = api_get(api_base, "signals/", {
            "min_score": min_score,
            "page": page,
            "page_size": page_size
        })
        results = data.get("results", [])
        if not results:
            break

        for item in results:
            all_rows.append({
                "tanggal": item.get("published_at") or item.get("crawled_at") or "",
                "penyakit_tag": item.get("disease_tag", "") or "",
                "skor_ancaman": str(item.get("threat_score", 0)),
                "judul": item.get("title", "") or "",
                "sumber": item.get("source_name", "") or "",
                "link": item.get("url") or item.get("final_url") or "",
                "geocode_status": "",
                "lokasi_mentah": "",
                "lat": "",
                "lon": "",
            })

        if not data.get("next"):
            break
        page += 1

    return all_rows


@st.cache_data(ttl=60)
def fetch_points(api_base, min_score):
    data = api_get(api_base, "points/", {"min_score": min_score})
    rows = []
    for item in data:
        rows.append({
            "tanggal": item.get("created_at") or "",
            "penyakit_tag": item.get("disease_tag", "") or "",
            "skor_ancaman": str(item.get("threat_score", 0)),
            "judul": item.get("title", "") or "",
            "sumber": item.get("source_name", "") or "",
            "link": item.get("link") or "",
            "lokasi_mentah": item.get("raw_location_text", "") or "",
            "geocode_status": item.get("geocode_status", "") or "",
            "lat": str(item.get("lat") or ""),
            "lon": str(item.get("lon") or ""),
        })
    return rows


@st.cache_data(ttl=60)
def fetch_errors(api_base, min_score=35, limit=200):
    return api_get(api_base, "errors/", {
        "min_score": min_score,
        "only_primary": 1,
        "limit": limit
    })


@st.cache_data(ttl=60)
def fetch_gazetteer_missing(api_base, min_score=35, limit=100):
    return api_get(api_base, "gazetteer/missing/", {
        "min_score": min_score,
        "only_primary": 1,
        "limit": limit
    })


# =========================
# SIDEBAR
# =========================
st.sidebar.header("Data Source")
mode = st.sidebar.radio("Ambil data dari", ["CSV", "API"], index=0)

api_base = DEFAULT_API_BASE
if mode == "API":
    api_base = st.sidebar.text_input("API Base URL", DEFAULT_API_BASE)

st.sidebar.divider()
st.sidebar.header("Filter")
min_score = st.sidebar.slider("Skor minimal", 0, 100, 35, 5)


# =========================
# LOAD DATA
# =========================
if mode == "CSV":
    raw_rows = load_csv(RAW_PATH)
    geo_rows = load_csv(GEO_PATH)
else:
    try:
        raw_rows = fetch_signals(api_base, min_score=0)  # load all for tag options
        geo_rows = fetch_points(api_base, min_score=min_score)
    except Exception as e:
        st.error(f"Gagal ambil data dari API: {e}")
        st.stop()

# Build tags AFTER load
all_tags = sorted({
    (r.get("penyakit_tag") or "").strip()
    for r in raw_rows
    if (r.get("penyakit_tag") or "").strip()
})

if all_tags:
    selected_tags = st.sidebar.multiselect("Penyakit", all_tags, default=all_tags)
else:
    st.sidebar.warning("Tidak ada penyakit_tag pada data (cek crawling/DB).")
    selected_tags = []


def pass_tag(r):
    if not selected_tags:
        return True
    return (r.get("penyakit_tag") or "").strip() in selected_tags


# Filter raw by tag + min_score
filtered_raw = [
    r for r in raw_rows
    if pass_tag(r) and parse_int(r.get("skor_ancaman", 0)) >= min_score
]

# Geo filter + bbox
filtered_geo = []
outliers = []
for r in geo_rows:
    if not pass_tag(r):
        continue
    if not has_latlon(r):
        continue
    lat = safe_float(r.get("lat"))
    lon = safe_float(r.get("lon"))
    if lat is None or lon is None:
        continue
    if in_indonesia_bbox(lat, lon):
        filtered_geo.append(r)
    else:
        outliers.append({
            "penyakit_tag": r.get("penyakit_tag", ""),
            "skor_ancaman": r.get("skor_ancaman", ""),
            "lokasi_mentah": r.get("lokasi_mentah", ""),
            "lat": lat,
            "lon": lon,
            "judul": (r.get("judul", "") or "")[:120],
        })


# =========================
# METRICS
# =========================
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Sinyal", len(filtered_raw))
c2.metric("Geocoded (ID bbox)", len(filtered_geo))
c3.metric("Tanpa Koordinat", max(len(filtered_raw) - len(filtered_geo), 0))
c4.metric("Outlier coords", len(outliers))

# =========================
# TABS
# =========================
tab_table, tab_map, tab_line, tab_bar, tab_errors = st.tabs(
    ["Table", "Map", "Line", "Bar", "Errors"]
)

with tab_table:
    st.subheader("Daftar Sinyal")
    table_rows = []
    for r in filtered_raw:
        table_rows.append({
            "tanggal": clean_for_table(r.get("tanggal")),
            "penyakit_tag": clean_for_table(r.get("penyakit_tag")),
            "skor_ancaman": clean_for_table(r.get("skor_ancaman")),
            "judul": clean_for_table(r.get("judul")),
            "sumber": clean_for_table(r.get("sumber")),
            "link": clean_for_table(r.get("link")),
        })
    st.dataframe(table_rows, use_container_width=True, height=600)

with tab_map:
    st.subheader("Peta Titik Kejadian")

    center_lat, center_lon, zoom = -2.5489, 118.0149, 5
    if filtered_geo:
        lats = [safe_float(r.get("lat")) for r in filtered_geo if safe_float(r.get("lat")) is not None]
        lons = [safe_float(r.get("lon")) for r in filtered_geo if safe_float(r.get("lon")) is not None]
        if lats and lons:
            center_lat = median(lats) or center_lat
            center_lon = median(lons) or center_lon
            zoom = 5 if len(filtered_geo) > 10 else 6

    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="OSM").add_to(m)
    folium.TileLayer("CartoDB positron", name="Positron").add_to(m)

    cluster = MarkerCluster(name="Clusters").add_to(m)

    for r in filtered_geo:
        lat = safe_float(r.get("lat"))
        lon = safe_float(r.get("lon"))
        if lat is None or lon is None:
            continue

        tag = r.get("penyakit_tag", "")
        skor = r.get("skor_ancaman", "")
        loc = r.get("lokasi_mentah", "")
        src = r.get("sumber", "")
        title = r.get("judul", "")
        link = r.get("link", "")

        popup = folium.Popup(
            f"""
            <b>{tag}</b> | Skor: <b>{skor}</b><br>
            Lokasi: {loc}<br>
            Sumber: {src}<br><br>
            {title}<br><br>
            <a href="{link}" target="_blank">Buka berita</a>
            """,
            max_width=350
        )

        folium.CircleMarker(
            location=[lat, lon],
            radius=6,
            popup=popup,
            tooltip=f"{tag} | {loc} | skor {skor}",
            fill=True
        ).add_to(cluster)

    folium.LayerControl(collapsed=False).add_to(m)
    st_folium(m, height=700, width=None)

    if outliers:
        st.warning("Ada koordinat outlier (di luar Indonesia).")
        st.dataframe(outliers[:50], use_container_width=True, height=240)

with tab_line:
    st.subheader("Trend Sinyal per Hari")

    daily = Counter()
    for r in filtered_raw:
        d = parse_date_any(r.get("tanggal"))
        if d:
            daily[d] += 1

    if daily:
        dates = sorted(daily.keys())
        chart = {"date": dates, "count": [daily[d] for d in dates]}
        st.line_chart(chart, x="date", y="count")
    else:
        st.warning("Tidak ada data tanggal valid.")

with tab_bar:
    st.subheader("Distribusi Sinyal")

    by_tag = Counter((r.get("penyakit_tag") or "Unknown") for r in filtered_raw)
    series = [{"penyakit_tag": k, "count": v} for k, v in by_tag.most_common()]

    st.bar_chart(
        {"penyakit_tag": [x["penyakit_tag"] for x in series], "count": [x["count"] for x in series]},
        x="penyakit_tag", y="count"
    )
    st.dataframe(series, use_container_width=True, height=350)

with tab_errors:
    if mode != "API":
        st.info("Tab Errors hanya aktif pada mode API.")
    else:
        st.subheader("Geocode Error Center")

        errors_data = fetch_errors(api_base, min_score=min_score)
        missing_data = fetch_gazetteer_missing(api_base, min_score=min_score)

        st.markdown("### Ringkasan Error (by geocode_status)")
        st.dataframe(errors_data.get("summary", []), use_container_width=True)

        st.markdown("### Detail Error (Top)")
        st.dataframe(errors_data.get("rows", []), use_container_width=True, height=420)

        st.markdown("### Prioritas Update Gazetteer (Missing Aliases)")
        st.dataframe(missing_data.get("results", []), use_container_width=True, height=420)