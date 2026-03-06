import re
from collections import Counter
from urllib.parse import urljoin

import requests
import streamlit as st
import folium
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

st.set_page_config(page_title="MedIntel-ID | OSINT System", layout="wide")
st.title("MedIntel-ID | OSINT System")

DEFAULT_API_BASE = "http://127.0.0.1:8000/api/"

# Indonesia bbox (anti outlier)
ID_LAT_MIN, ID_LAT_MAX = -11.5, 6.5
ID_LON_MIN, ID_LON_MAX = 95.0, 141.5


# =========================
# HELPERS
# =========================
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


def api_get(api_base, path, params=None):
    url = urljoin(api_base, path)
    r = requests.get(url, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


# =========================
# API FETCHERS
# =========================
@st.cache_data(ttl=60)
def fetch_signals(api_base, min_score=0):
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
                "id": item.get("id"),
                "tanggal": item.get("published_at") or item.get("crawled_at") or "",
                "penyakit_tag": item.get("disease_tag", "") or "",
                "detected_diseases": item.get("detected_diseases", "") or "",
                "event_types": item.get("event_types", "") or "",
                "severity_nlp": item.get("severity_nlp", 0) or 0,
                "skor_ancaman": item.get("threat_score", 0) or 0,
                "judul": item.get("title", "") or "",
                "sumber": item.get("source_name", "") or "",
                "link": item.get("url") or item.get("final_url") or "",
                "status": item.get("status", "") or "",
            })

        if not data.get("next"):
            break
        page += 1

    return all_rows


@st.cache_data(ttl=60)
def fetch_points(api_base, min_score=0):
    data = api_get(api_base, "points/", {"min_score": min_score})
    rows = []

    for item in data:
        rows.append({
            "id": item.get("id"),
            "signal_id": item.get("signal_id"),
            "tanggal": item.get("created_at") or "",
            "penyakit_tag": item.get("disease_tag", "") or "",
            "detected_diseases": item.get("detected_diseases", "") or "",
            "event_types": item.get("event_types", "") or "",
            "severity_nlp": item.get("severity_nlp", 0) or 0,
            "skor_ancaman": item.get("threat_score", 0) or 0,
            "judul": item.get("title", "") or "",
            "sumber": item.get("source_name", "") or "",
            "link": item.get("link", "") or "",
            "lokasi_mentah": item.get("raw_location_text", "") or "",
            "geocode_status": item.get("geocode_status", "") or "",
            "lat": item.get("lat"),
            "lon": item.get("lon"),
            "location_level": item.get("location_level"),
            "admin_province": item.get("admin_province"),
            "admin_kabkota": item.get("admin_kabkota"),
        })

    return rows


@st.cache_data(ttl=60)
def fetch_province_points(api_base, min_score=35):
    return api_get(api_base, "agg/province-points/", params={"min_score": min_score})


@st.cache_data(ttl=60)
def fetch_trend(api_base, min_score=35):
    return api_get(api_base, "agg/trend/", {"min_score": min_score})


@st.cache_data(ttl=60)
def fetch_stats(api_base, min_score=35):
    return api_get(api_base, "stats/", {"min_score": min_score})


@st.cache_data(ttl=60)
def fetch_alerts(api_base):
    return api_get(api_base, "alerts/outbreak/")


# =========================
# SIDEBAR
# =========================
st.sidebar.header("API Configuration")
api_base = st.sidebar.text_input("API Base URL", DEFAULT_API_BASE)

st.sidebar.divider()
st.sidebar.header("Filter")
min_score = st.sidebar.slider("Skor minimal", 0, 100, 35, 5)

# =========================
# LOAD DATA
# =========================
try:
    raw_rows = fetch_signals(api_base, min_score=0)   # ambil semua buat filter tag
    geo_rows = fetch_points(api_base, min_score=min_score)
    stats_data = fetch_stats(api_base, min_score=min_score)
except Exception as e:
    st.error(f"Gagal ambil data dari API: {e}")
    st.stop()

# Build tag list
all_tags = sorted({
    (r.get("penyakit_tag") or "").strip()
    for r in raw_rows
    if (r.get("penyakit_tag") or "").strip()
})

selected_tags = st.sidebar.multiselect(
    "Penyakit",
    all_tags,
    default=all_tags
)

def pass_tag(r):
    if not selected_tags:
        return True
    return (r.get("penyakit_tag") or "").strip() in selected_tags


# =========================
# FILTERING
# =========================
filtered_raw = [
    r for r in raw_rows
    if pass_tag(r) and parse_int(r.get("skor_ancaman", 0)) >= min_score
]

filtered_geo = []
outliers = []

for r in geo_rows:
    if not pass_tag(r):
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
tab_table, tab_map, tab_line, tab_bar, tab_alert = st.tabs(
    ["Table", "Map", "Line", "Bar", "Alerts"]
)

# =========================
# TABLE
# =========================
with tab_table:
    st.subheader("Daftar Sinyal")

    table_rows = []
    for r in filtered_raw:
        table_rows.append({
            "tanggal": clean_for_table(r.get("tanggal")),
            "penyakit_tag": clean_for_table(r.get("penyakit_tag")),
            "detected_diseases": clean_for_table(r.get("detected_diseases")),
            "event_types": clean_for_table(r.get("event_types")),
            "severity_nlp": r.get("severity_nlp", 0),
            "skor_ancaman": r.get("skor_ancaman", 0),
            "status": clean_for_table(r.get("status")),
            "judul": clean_for_table(r.get("judul")),
            "sumber": clean_for_table(r.get("sumber")),
            "link": clean_for_table(r.get("link")),
        })

    st.dataframe(table_rows, use_container_width=True, height=600)

# =========================
# MAP
# =========================
# with tab_map:
#     st.subheader("Peta")

#     map_mode = st.radio(
#         "Mode peta",
#         ["Titik (Cluster)", "Tematik Provinsi (Marker)"],
#         horizontal=True
#     )

#     if map_mode == "Titik (Cluster)":
#         valid_points = []
#         local_outliers = []

#         for r in filtered_geo:
#             lat = safe_float(r.get("lat"))
#             lon = safe_float(r.get("lon"))
#             if lat is None or lon is None:
#                 continue

#             if not in_indonesia_bbox(lat, lon):
#                 local_outliers.append(r)
#                 continue

#             valid_points.append((r, lat, lon))

#         st.write(f"Total titik valid (cluster): **{len(valid_points)}**")

#         m = folium.Map(location=[-2.5489, 118.0149], zoom_start=5, control_scale=True)
#         folium.TileLayer("OpenStreetMap", name="OSM").add_to(m)
#         folium.TileLayer("CartoDB positron", name="Positron").add_to(m)

#         cluster = MarkerCluster(name="Clusters").add_to(m)

#         lats = []
#         lons = []

#         for r, lat, lon in valid_points:
#             tag = r.get("penyakit_tag", "")
#             skor = r.get("skor_ancaman", "")
#             loc = r.get("lokasi_mentah", "")
#             src = r.get("sumber", "")
#             title = r.get("judul", "")
#             link = r.get("link", "")
#             province = r.get("admin_province", "")
#             kabkota = r.get("admin_kabkota", "")

#             popup = folium.Popup(
#                 f"""
#                 <b>{tag}</b> | Skor: <b>{skor}</b><br>
#                 Lokasi: {loc}<br>
#                 Provinsi: {province}<br>
#                 Kab/Kota: {kabkota}<br>
#                 Sumber: {src}<br><br>
#                 {title}<br><br>
#                 <a href="{link}" target="_blank">Buka berita</a>
#                 """,
#                 max_width=350
#             )

#             folium.CircleMarker(
#                 location=[lat, lon],
#                 radius=6,
#                 popup=popup,
#                 tooltip=f"{tag} | {loc} | skor {skor}",
#                 fill=True
#             ).add_to(cluster)

#             lats.append(lat)
#             lons.append(lon)

#         if lats and lons:
#             m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
#         else:
#             st.info("Tidak ada titik valid untuk ditampilkan.")

#         folium.LayerControl(collapsed=False).add_to(m)
#         st_folium(m, height=700, width=None)

#         if local_outliers:
#             st.warning("Ada koordinat outlier (di luar bbox Indonesia).")
#             st.dataframe(local_outliers[:50], use_container_width=True, height=240)

#     else:
#         prov_rows = fetch_province_points(api_base, min_score=min_score)

#         if selected_tags:
#             # endpoint agg belum difilter by tag, jadi sementara thematic tetap global
#             st.caption("Catatan: mode tematik provinsi saat ini masih agregasi global berdasarkan min_score.")

#         prov_rows = [
#             r for r in prov_rows
#             if safe_float(r.get("lat")) is not None and safe_float(r.get("lon")) is not None
#         ]

#         st.write(f"Total provinsi terplot: **{len(prov_rows)}**")

#         metric = st.selectbox("Ukuran marker berdasarkan", ["count", "avg_score", "max_score"], index=0)

#         center_lat, center_lon, zoom = -2.5489, 118.0149, 5
#         if prov_rows:
#             center_lat = sum(float(r["lat"]) for r in prov_rows) / len(prov_rows)
#             center_lon = sum(float(r["lon"]) for r in prov_rows) / len(prov_rows)

#         m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom, control_scale=True)
#         folium.TileLayer("CartoDB positron", name="Positron").add_to(m)
#         folium.TileLayer("OpenStreetMap", name="OSM").add_to(m)

#         def pick_color(v):
#             try:
#                 v = float(v)
#             except Exception:
#                 v = 0.0

#             if metric == "count":
#                 if v >= 30: return "#800026"
#                 if v >= 15: return "#BD0026"
#                 if v >= 8:  return "#E31A1C"
#                 if v >= 3:  return "#FC4E2A"
#                 if v >= 1:  return "#FD8D3C"
#                 return "#FFEDA0"
#             else:
#                 if v >= 80: return "#800026"
#                 if v >= 60: return "#BD0026"
#                 if v >= 40: return "#E31A1C"
#                 if v >= 25: return "#FC4E2A"
#                 if v >= 10: return "#FD8D3C"
#                 return "#FFEDA0"

#         vals = [float(r.get(metric, 0) or 0) for r in prov_rows] if prov_rows else []
#         maxv = max(vals) if vals else 1

#         lats = []
#         lons = []

#         for r in prov_rows:
#             lat = safe_float(r.get("lat"))
#             lon = safe_float(r.get("lon"))
#             if lat is None or lon is None:
#                 continue

#             province = r.get("province", "")
#             count = r.get("count", 0)
#             avg_score = r.get("avg_score", 0)
#             max_score = r.get("max_score", 0)

#             val = float(r.get(metric, 0) or 0)
#             radius = 6 + (16 * (val / maxv)) if maxv else 8
#             color = pick_color(val)

#             popup = folium.Popup(
#                 f"<b>{province}</b><br>"
#                 f"Count: <b>{count}</b><br>"
#                 f"Avg score: <b>{avg_score}</b><br>"
#                 f"Max score: <b>{max_score}</b><br>",
#                 max_width=300
#             )

#             folium.CircleMarker(
#                 location=[lat, lon],
#                 radius=radius,
#                 color=color,
#                 fill=True,
#                 fill_color=color,
#                 fill_opacity=0.65,
#                 popup=popup,
#                 tooltip=f"{province} | count {count} | max {max_score}"
#             ).add_to(m)

#             lats.append(lat)
#             lons.append(lon)

#         if lats and lons:
#             m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])

#         folium.LayerControl(collapsed=False).add_to(m)
#         st_folium(m, height=700, width=None)

#         st.divider()
#         st.caption("Tabel agregasi provinsi")
#         st.dataframe(prov_rows, use_container_width=True, height=420)

with tab_map:
    st.subheader("Peta")

    map_mode = st.radio(
        "Mode peta",
        ["Titik (Cluster)", "Tematik Provinsi (Marker)"],
        horizontal=True
    )

    # ambil alert payload untuk integrasi map
    try:
        alert_payload = api_get(api_base, "alerts/outbreak/", {
            "min_score": min_score,
            "recent_days": 7,
            "baseline_days": 30,
        })
        map_alerts = alert_payload.get("results", [])
    except Exception:
        map_alerts = []

    if selected_tags:
        map_alerts = [a for a in map_alerts if a.get("disease") in selected_tags]

    # mapping alert by province
    province_alert_map = {}
    for a in map_alerts:
        prov = (a.get("province") or "").strip()
        if not prov:
            continue

        # simpan alert tertinggi per provinsi
        if prov not in province_alert_map:
            province_alert_map[prov] = a
        else:
            prev = province_alert_map[prov]
            prev_score = prev.get("risk_score", 0) or 0
            curr_score = a.get("risk_score", 0) or 0
            if curr_score > prev_score:
                province_alert_map[prov] = a

    def alert_color(level):
        if level == "CRITICAL":
            return "#8B0000"
        elif level == "HIGH":
            return "#FF8C00"
        elif level == "MEDIUM":
            return "#1E90FF"
        return "#2E8B57"

    # -------------------------
    # A) TITIK (CLUSTER)
    # -------------------------
    if map_mode == "Titik (Cluster)":
        valid_points = []
        local_outliers = []

        for r in filtered_geo:
            lat = safe_float(r.get("lat"))
            lon = safe_float(r.get("lon"))
            if lat is None or lon is None:
                continue

            if not in_indonesia_bbox(lat, lon):
                local_outliers.append(r)
                continue

            valid_points.append((r, lat, lon))

        st.write(f"Total titik valid (cluster): **{len(valid_points)}**")

        m = folium.Map(location=[-2.5489, 118.0149], zoom_start=5, control_scale=True)
        folium.TileLayer("OpenStreetMap", name="OSM").add_to(m)
        folium.TileLayer("CartoDB positron", name="Positron").add_to(m)

        cluster = MarkerCluster(name="Clusters").add_to(m)

        lats = []
        lons = []

        for r, lat, lon in valid_points:
            tag = r.get("penyakit_tag", "")
            skor = r.get("skor_ancaman", "")
            loc = r.get("lokasi_mentah", "")
            src = r.get("sumber", "")
            title = r.get("judul", "")
            link = r.get("link", "")
            province = r.get("admin_province", "")
            kabkota = r.get("admin_kabkota", "")

            alert_info = province_alert_map.get(province)
            alert_note = ""
            if alert_info:
                alert_note = (
                    f"<br><b>ALERT PROVINSI</b>: {alert_info.get('risk_level')} | "
                    f"{alert_info.get('disease')} | score={alert_info.get('risk_score')}"
                )

            popup = folium.Popup(
                f"""
                <b>{tag}</b> | Skor: <b>{skor}</b><br>
                Lokasi: {loc}<br>
                Provinsi: {province}<br>
                Kab/Kota: {kabkota}<br>
                Sumber: {src}<br>
                {alert_note}<br><br>
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

            lats.append(lat)
            lons.append(lon)

        if lats and lons:
            m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
        else:
            st.info("Tidak ada titik valid untuk ditampilkan.")

        folium.LayerControl(collapsed=False).add_to(m)
        st_folium(m, height=700, width=None)

        if local_outliers:
            st.warning("Ada koordinat outlier (di luar bbox Indonesia).")
            st.dataframe(local_outliers[:50], use_container_width=True, height=220)

    # -------------------------
    # B) TEMATIK PROVINSI (MARKER)
    # -------------------------
    else:
        prov_rows = fetch_province_points(api_base, min_score=min_score)

        if selected_tags:
            st.caption("Catatan: agregasi provinsi saat ini masih global berdasarkan min_score.")

        prov_rows = [
            r for r in prov_rows
            if safe_float(r.get("lat")) is not None and safe_float(r.get("lon")) is not None
        ]

        st.write(f"Total provinsi terplot: **{len(prov_rows)}**")

        metric = st.selectbox(
            "Ukuran marker berdasarkan",
            ["count", "avg_score", "max_score"],
            index=0,
            key="map_prov_metric"
        )

        center_lat, center_lon, zoom = -2.5489, 118.0149, 5
        if prov_rows:
            center_lat = sum(float(r["lat"]) for r in prov_rows) / len(prov_rows)
            center_lon = sum(float(r["lon"]) for r in prov_rows) / len(prov_rows)

        m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom, control_scale=True)
        folium.TileLayer("CartoDB positron", name="Positron").add_to(m)
        folium.TileLayer("OpenStreetMap", name="OSM").add_to(m)

        vals = [float(r.get(metric, 0) or 0) for r in prov_rows] if prov_rows else []
        maxv = max(vals) if vals else 1

        lats = []
        lons = []

        for r in prov_rows:
            lat = safe_float(r.get("lat"))
            lon = safe_float(r.get("lon"))
            if lat is None or lon is None:
                continue

            province = r.get("province", "")
            count = r.get("count", 0)
            avg_score = r.get("avg_score", 0)
            max_score = r.get("max_score", 0)

            val = float(r.get(metric, 0) or 0)
            radius = 6 + (16 * (val / maxv)) if maxv else 8

            # kalau provinsi ini punya alert, warna ikut risk level
            alert_info = province_alert_map.get(province)
            if alert_info:
                color = alert_color(alert_info.get("risk_level"))
                alert_block = (
                    f"<br><b>ALERT</b>: {alert_info.get('risk_level')}<br>"
                    f"Disease: {alert_info.get('disease')}<br>"
                    f"Risk score: {alert_info.get('risk_score')}<br>"
                    f"Increase ratio: {alert_info.get('increase_ratio')}<br>"
                    f"Recent/Baseline: {alert_info.get('recent')} / {alert_info.get('baseline')}<br>"
                    f"Top event: {alert_info.get('top_event_type')}<br>"
                )
            else:
                # warna normal kalau belum ada alert
                if metric == "count":
                    if val >= 30: color = "#800026"
                    elif val >= 15: color = "#BD0026"
                    elif val >= 8: color = "#E31A1C"
                    elif val >= 3: color = "#FC4E2A"
                    elif val >= 1: color = "#FD8D3C"
                    else: color = "#FFEDA0"
                else:
                    if val >= 80: color = "#800026"
                    elif val >= 60: color = "#BD0026"
                    elif val >= 40: color = "#E31A1C"
                    elif val >= 25: color = "#FC4E2A"
                    elif val >= 10: color = "#FD8D3C"
                    else: color = "#FFEDA0"
                alert_block = "<br><i>Tidak ada alert aktif</i><br>"

            popup = folium.Popup(
                f"<b>{province}</b><br>"
                f"Count: <b>{count}</b><br>"
                f"Avg score: <b>{avg_score}</b><br>"
                f"Max score: <b>{max_score}</b><br>"
                f"{alert_block}",
                max_width=320
            )

            folium.CircleMarker(
                location=[lat, lon],
                radius=radius,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.7,
                popup=popup,
                tooltip=f"{province} | count={count} | max={max_score}"
            ).add_to(m)

            lats.append(lat)
            lons.append(lon)

        if lats and lons:
            m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])

        folium.LayerControl(collapsed=False).add_to(m)
        st_folium(m, height=700, width=None)

        st.markdown("### Alert Provinsi Aktif")
        active_alert_rows = []
        for prov, a in province_alert_map.items():
            active_alert_rows.append({
                "province": prov,
                "risk_level": a.get("risk_level"),
                "risk_score": a.get("risk_score"),
                "disease": a.get("disease"),
                "increase_ratio": a.get("increase_ratio"),
                "recent": a.get("recent"),
                "baseline": a.get("baseline"),
                "top_event_type": a.get("top_event_type"),
            })

        active_alert_rows = sorted(
            active_alert_rows,
            key=lambda x: x.get("risk_score", 0),
            reverse=True
        )

        if active_alert_rows:
            st.dataframe(active_alert_rows, use_container_width=True, height=300)
        else:
            st.info("Belum ada alert provinsi aktif.")

        st.divider()
        st.caption("Tabel agregasi provinsi")
        st.dataframe(prov_rows, use_container_width=True, height=320)
# =========================
# LINE
# =========================
with tab_line:
    st.subheader("Trend Sinyal")

    try:
        trend_data = fetch_trend(api_base, min_score=min_score)
    except Exception as e:
        st.error(f"Gagal ambil trend: {e}")
        trend_data = []

    if not trend_data:
        st.warning("Tidak ada data")
    else:
        chart_data = {
            "date": [r["date"] for r in trend_data],
            "count": [r["count"] for r in trend_data]
        }
        st.line_chart(chart_data, x="date", y="count")
        st.dataframe(trend_data, use_container_width=True)

# =========================
# BAR
# =========================
with tab_bar:
    st.subheader("Distribusi Sinyal")

    by_tag = Counter((r.get("penyakit_tag") or "Unknown") for r in filtered_raw)
    disease_rows = [{"penyakit_tag": k, "count": v} for k, v in by_tag.most_common()]

    st.markdown("**Distribusi per penyakit**")
    st.bar_chart(
        {
            "penyakit_tag": [x["penyakit_tag"] for x in disease_rows],
            "count": [x["count"] for x in disease_rows]
        },
        x="penyakit_tag",
        y="count"
    )
    st.dataframe(disease_rows, use_container_width=True, height=260)

    event_counter = Counter()
    for r in filtered_raw:
        evs = (r.get("event_types") or "").strip()
        if not evs:
            continue
        for ev in evs.split("|"):
            ev = ev.strip()
            if ev:
                event_counter[ev] += 1

    if event_counter:
        st.markdown("**Distribusi per event type**")
        event_rows = [{"event_type": k, "count": v} for k, v in event_counter.most_common()]
        st.bar_chart(
            {
                "event_type": [x["event_type"] for x in event_rows],
                "count": [x["count"] for x in event_rows]
            },
            x="event_type",
            y="count"
        )
        st.dataframe(event_rows, use_container_width=True, height=220)

# =========================
# ALERTS
# =========================
with tab_alert:
    st.subheader("Outbreak Alerts")

    recent_days = st.slider("Recent window (hari)", 1, 14, 7, key="alert_recent_days")
    baseline_days = st.slider("Baseline window (hari)", 7, 60, 30, key="alert_baseline_days")

    try:
        alert_payload = api_get(api_base, "alerts/outbreak/", {
            "min_score": min_score,
            "recent_days": recent_days,
            "baseline_days": baseline_days,
        })
        summary = alert_payload.get("summary", {})
        alerts = alert_payload.get("results", [])
    except Exception as e:
        st.error(f"Gagal ambil alerts: {e}")
        summary = {}
        alerts = []

    # ---------------------------------
    # Extra filters
    # ---------------------------------
    all_provinces = sorted({
        (a.get("province") or "").strip()
        for a in alerts
        if (a.get("province") or "").strip()
    })

    selected_provinces = st.multiselect(
        "Filter provinsi",
        all_provinces,
        default=all_provinces,
        key="alert_selected_provinces"
    )

    sort_by = st.selectbox(
        "Urutkan berdasarkan",
        ["risk_score", "increase_ratio", "recent"],
        index=0,
        key="alert_sort_by"
    )

    if selected_tags:
        alerts = [a for a in alerts if a.get("disease") in selected_tags]

    if selected_provinces:
        alerts = [a for a in alerts if a.get("province") in selected_provinces]

    alerts = sorted(
        alerts,
        key=lambda x: x.get(sort_by, 0),
        reverse=True
    )

    # ---------------------------------
    # Summary panel
    # ---------------------------------
    if summary:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Recent records", summary.get("total_recent_records", 0))
        c2.metric("Baseline records", summary.get("total_baseline_records", 0))
        c3.metric("Recent groups", summary.get("recent_group_count", 0))
        c4.metric("Alert count", len(alerts))

        st.caption(
            f"Window recent={summary.get('recent_days', recent_days)} hari | "
            f"baseline={summary.get('baseline_days', baseline_days)} hari | "
            f"min_score={summary.get('min_score', min_score)}"
        )

    # ---------------------------------
    # No alerts message
    # ---------------------------------
    if not alerts:
        if summary:
            if summary.get("total_recent_records", 0) == 0:
                st.warning(
                    "Tidak ada record pada recent window. "
                    "Coba perbesar recent window atau turunkan skor minimal."
                )
            elif summary.get("alert_count", 0) == 0:
                st.success(
                    "Ada data, tetapi belum ada kombinasi disease + province "
                    "yang memenuhi threshold outbreak."
                )
            else:
                st.success("Tidak ada sinyal outbreak setelah filter diterapkan.")
        else:
            st.success("Tidak ada sinyal outbreak.")
    else:
        st.write(f"Total alert terfilter: **{len(alerts)}**")

        # ---------------------------------
        # Risk cards
        # ---------------------------------
        for a in alerts:
            txt = (
                f"{a['risk_level']} | {a['disease']} | {a['province']} | "
                f"score={a['risk_score']} | ratio={a['increase_ratio']} | "
                f"recent={a['recent']} | baseline={a['baseline']} | "
                f"avg_score={a['avg_score_recent']} | severity={a['severity_avg']} | "
                f"top_event={a.get('top_event_type')}"
            )

            if a["risk_level"] == "CRITICAL":
                st.error(txt)
            elif a["risk_level"] == "HIGH":
                st.warning(txt)
            elif a["risk_level"] == "MEDIUM":
                st.info(txt)
            else:
                st.success(txt)

        # ---------------------------------
        # Clean dataframe
        # ---------------------------------
        alert_rows = []
        for a in alerts:
            alert_rows.append({
                "risk_level": a.get("risk_level"),
                "risk_score": a.get("risk_score"),
                "level": a.get("level"),
                "disease": a.get("disease"),
                "province": a.get("province"),
                "recent": a.get("recent"),
                "baseline": a.get("baseline"),
                "increase_ratio": a.get("increase_ratio"),
                "avg_score_recent": a.get("avg_score_recent"),
                "severity_avg": a.get("severity_avg"),
                "top_event_type": a.get("top_event_type"),
            })

        st.dataframe(alert_rows, use_container_width=True, height=420)

        # ---------------------------------
        # Risk distribution
        # ---------------------------------
        from collections import Counter

        risk_counter = Counter(a["risk_level"] for a in alerts)
        risk_rows = [{"risk_level": k, "count": v} for k, v in risk_counter.items()]

        st.subheader("Risk Level Distribution")
        st.bar_chart(
            {
                "risk_level": [x["risk_level"] for x in risk_rows],
                "count": [x["count"] for x in risk_rows],
            },
            x="risk_level",
            y="count"
        )

        st.dataframe(risk_rows, use_container_width=True, height=200)

        # ---------------------------------
        # Province distribution
        # ---------------------------------
        province_counter = Counter(a["province"] for a in alerts if a.get("province"))
        province_rows = [{"province": k, "count": v} for k, v in province_counter.most_common()]

        if province_rows:
            st.subheader("Distribusi Alert per Provinsi")
            st.bar_chart(
                {
                    "province": [x["province"] for x in province_rows],
                    "count": [x["count"] for x in province_rows],
                },
                x="province",
                y="count"
            )
            st.dataframe(province_rows, use_container_width=True, height=220)