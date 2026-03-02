import csv
import streamlit as st
import folium
from streamlit_folium import st_folium

st.set_page_config(page_title="MedIntel-ID | OSINT System", layout="wide")
st.title("MedIntel-ID | OSINT System")
st.caption("Pemetaan sinyal penyakit berbasis OSINT (RSS + full article) + Geocoding")

RAW_PATH = "../crawler/output/data_intel_raw.csv"
GEO_PATH = "../crawler/output/data_intel_geo.csv"


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


def has_latlon(r):
    try:
        lat = r.get("lat")
        lon = r.get("lon")
        return lat not in [None, "", "NA", "NaN"] and lon not in [None, "", "NA", "NaN"]
    except Exception:
        return False


raw_rows = load_csv(RAW_PATH)
geo_rows = load_csv(GEO_PATH)

# =========================
# SIDEBAR: MENU + FILTER
# =========================
st.sidebar.header("Menu")
page = st.sidebar.radio("Navigasi", ["Mapping", "Sinyal"], index=0)

st.sidebar.divider()
st.sidebar.header("Filter")

all_tags = sorted({r.get("penyakit_tag", "") for r in raw_rows if r.get("penyakit_tag")})
selected = st.sidebar.multiselect("Penyakit", all_tags, default=all_tags)

min_score = st.sidebar.slider("Skor minimal", 0, 100, 35, 5)


# =========================
# APPLY FILTERS
# =========================
filtered_raw = []
for r in raw_rows:
    tag = r.get("penyakit_tag", "")
    skor = parse_int(r.get("skor_ancaman", "0"), 0)
    if tag in selected and skor >= min_score:
        filtered_raw.append(r)

filtered_geo = []
for r in geo_rows:
    tag = r.get("penyakit_tag", "")
    skor = parse_int(r.get("skor_ancaman", "0"), 0)
    if tag in selected and skor >= min_score and has_latlon(r):
        filtered_geo.append(r)


# =========================
# PAGE: MAPPING
# =========================
if page == "Mapping":

    st.subheader("Peta Titik Kejadian (Geocoded)")
    st.write(f"Total titik terfilter: **{len(filtered_geo)}**")

    center_lat, center_lon, zoom = -2.5489, 118.0149, 5
    if filtered_geo:
        center_lat = sum(float(r["lat"]) for r in filtered_geo) / len(filtered_geo)
        center_lon = sum(float(r["lon"]) for r in filtered_geo) / len(filtered_geo)
        zoom = 5 if len(filtered_geo) > 10 else 6

    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="OSM").add_to(m)
    folium.TileLayer("CartoDB positron", name="Positron").add_to(m)

    for r in filtered_geo:
        lat, lon = float(r["lat"]), float(r["lon"])
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
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    st_folium(m, height=700, width=None)
# =========================
# PAGE: SINYAL (FULL TABLE)
# =========================
elif page == "Sinyal":
    st.subheader("Daftar Sinyal (hasil crawling)")
    st.write(f"Total sinyal terfilter: **{len(filtered_raw)}** | Dengan koordinat: **{len(filtered_geo)}**")

    # Tabel lengkap
    def build_full_table(rows):
        out = []
        for r in rows:
            out.append({
                "tanggal": r.get("tanggal", ""),
                "penyakit_tag": r.get("penyakit_tag", ""),
                "skor_ancaman": r.get("skor_ancaman", ""),
                "lokasi_mentah": r.get("lokasi_mentah", ""),
                "level_lokasi": r.get("level_lokasi", ""),
                "confidence_lokasi": r.get("confidence_lokasi", ""),
                "geocode_status": r.get("geocode_status", ""),
                "judul": r.get("judul", ""),
                "sumber": r.get("sumber", ""),
                "link": r.get("link", ""),
            })
        return out

    st.dataframe(build_full_table(filtered_raw), use_container_width=True, height=650)

    st.divider()
    st.subheader("Sinyal Tanpa Koordinat (untuk update gazetteer)")
    miss = [r for r in filtered_raw if not has_latlon(r)]
    st.write(f"Jumlah tanpa koordinat: **{len(miss)}**")

    # Kompilasi lokasi mentah yang sering gagal
    # (grouping manual tanpa pandas)
    counter = {}
    for r in miss:
        loc = (r.get("lokasi_mentah", "") or "").strip()
        if not loc:
            loc = "(NO_LOCATION_DETECTED)"
        counter[loc] = counter.get(loc, 0) + 1

    top = sorted(counter.items(), key=lambda x: x[1], reverse=True)[:50]
    st.caption("Top 50 lokasi gagal (semakin sering muncul, semakin prioritas ditambahkan ke gazetteer_id.csv)")
    st.dataframe([{"lokasi_mentah": k, "jumlah": v} for k, v in top], use_container_width=True, height=400)