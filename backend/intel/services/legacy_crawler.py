import re
import time
import html
import csv
import socket
import feedparser
import requests
from typing import Dict, List, Tuple
from intel.services.nlp_extractor import extract_entities
from bs4 import BeautifulSoup
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from geopy.exc import (
    GeocoderTimedOut,
    GeocoderUnavailable,
    GeocoderServiceError,
    GeocoderQuotaExceeded,
)

from intel.models import Location, LocationAlias


# =========================
# UTIL
# =========================
def clean_text(x: str) -> str:
    """Remove html tags, decode entities, normalize whitespace."""
    if x is None:
        return ""
    x = html.unescape(str(x))
    x = re.sub(r"<[^>]+>", " ", x)
    x = re.sub(r"\s+", " ", x).strip()
    return x


def truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n].rstrip() + "..."


def normalize_location_name(text: str) -> str:
    if not text:
        return ""

    text = clean_text(text).lower().strip()

    replacements = {
        "kab.": "kabupaten",
        "kab ": "kabupaten ",
        "kotamadya": "kota",
        "kodya": "kota",
        "prov.": "provinsi",
        "prov ": "provinsi ",
        "dki": "dki jakarta",
        "di yogyakarta": "yogyakarta",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"[^a-z0-9\s\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def norm(s: str) -> str:
    """Normalize for matching."""
    return normalize_location_name(s)


def contains_phrase(text_norm: str, phrase_norm: str) -> bool:
    """Word-boundary phrase match."""
    if not phrase_norm:
        return False
    return re.search(rf"\b{re.escape(phrase_norm)}\b", text_norm) is not None


# =========================
# GAZETTEER
# =========================
def load_gazetteer() -> List[Dict]:
    entries: List[Dict] = []

    locations = Location.objects.filter(
        is_active=True,
        is_false_positive=False,
    ).select_related("parent")

    alias_map: Dict[int, List[str]] = {}
    for a in LocationAlias.objects.filter(
        is_active=True,
        location__is_active=True,
        location__is_false_positive=False,
    ).select_related("location"):
        alias_map.setdefault(a.location_id, []).append(clean_text(a.alias))

    for loc in locations:
        name = clean_text(loc.display_name or loc.name or "")
        level = clean_text(loc.level or "").lower().strip()

        province = ""
        if level in ["city", "regency", "kota", "kabupaten"] and loc.parent:
            province = clean_text(loc.parent.display_name or loc.parent.name or "")
        elif level in ["province", "provinsi"]:
            province = clean_text(loc.display_name or loc.name or "")

        aliases = alias_map.get(loc.id, [])

        forms = [name] + aliases

        # tambahkan variasi tanpa prefix supaya match lebih fleksibel
        if level in ["city", "kota"] and name.lower().startswith("kota "):
            forms.append(name[5:].strip())

        if level in ["regency", "kabupaten"]:
            lowered = name.lower()
            if lowered.startswith("kabupaten "):
                forms.append(name[10:].strip())
            elif lowered.startswith("kab "):
                forms.append(name[4:].strip())
            elif lowered.startswith("kab. "):
                forms.append(name[5:].strip())

        forms_norm = sorted(
            {norm(x) for x in forms if norm(x)},
            key=len,
            reverse=True
        )

        admin_kabkota = ""
        admin_province = ""

        if level in ["kota", "kabupaten", "city", "regency"]:
            admin_kabkota = name
            admin_province = province
        elif level in ["province", "provinsi"]:
            admin_province = name

        entries.append({
            "name": name,
            "level": level,
            "province": province,
            "forms_norm": forms_norm,
            "admin_province": admin_province,
            "admin_kabkota": admin_kabkota,
        })

    prio = {
        "kota": 4,
        "city": 4,
        "kabupaten": 3,
        "regency": 3,
        "province": 2,
        "provinsi": 2,
    }

    entries.sort(
        key=lambda e: (
            prio.get(e["level"], 0),
            max((len(x) for x in e["forms_norm"]), default=0)
        ),
        reverse=True
    )
    return entries

# =========================
# LOCATION VALIDATION
# =========================
def validate_location(loc: str) -> Tuple[bool, str]:
    loc = (loc or "").strip()
    if not loc:
        return True, "EMPTY_LOC"

    low = loc.lower().strip()

    too_general_exact = {
        "indonesia", "ri", "nusantara", "nasional", "global", "dunia",
        "asia", "eropa", "amerika", "afrika", "australia"
    }
    if low in too_general_exact:
        return True, "SKIP_TOO_GENERAL"

    junk_exact = {
        "ini", "awal", "video", "update", "terbaru", "hari ini", "kemarin",
        "kemenkes", "kementerian", "pemerintah", "id", "co", "com"
    }
    if low in junk_exact:
        return True, "SKIP_NOISE"

    if len(low) < 4:
        return True, "SKIP_NOISE"

    bad_contains = ["kasus", "wabah", "meningkat", "waspada", "darurat", "kematian", "lonjakan", "suspek"]
    if any(b in low for b in bad_contains):
        return True, "SKIP_NOISE"

    if low.count("indonesia") >= 2:
        return True, "SKIP_TOO_GENERAL"

    return False, ""


# =========================
# CRAWLER
# =========================
class MedIntelCrawler:
    def __init__(self):
        self.keywords = [
            "DBD", "Mpox", "Flu Burung", "Antraks", "KLB Penyakit", "Campak",
            "Rubela", "Diare", "Kolera", "Demam Tifoid", "Chikungunya", "Superflu",
            "Avian Influenza", "Nipah", "TBC", "HIV", "AIDS", "IMS",
            "Infeksi Menular Seksual", "Polio", "Difteri", "Batuk Renjan",
            "Pertusis", "Rabies", "Leptospirosis", "Antraks"
        ]
        self.rss_url = "https://news.google.com/rss/search?q={query}+when:7d&hl=id&gl=ID&ceid=ID:id"

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (MedIntel-ID; +https://example.local)"
        })
        self.fetch_timeout = 12

        self.geolocator = Nominatim(user_agent="medintel-id-crawler")
        self.geocode = RateLimiter(
            self.geolocator.geocode,
            min_delay_seconds=1,
            return_value_on_exception=None
        )

        self._article_cache: Dict[str, str] = {}
        self._geocode_cache: Dict[str, Tuple[str, str, str]] = {}

        # self.gazetteer = load_gazetteer("gazetteer_id.csv")
        from pathlib import Path
        BASE_DIR = Path(__file__).resolve().parent
        GAZETTEER_PATH = BASE_DIR.parent / "services" / "gazetteer_id.csv"

        # self.gazetteer = load_gazetteer(str(GAZETTEER_PATH))
        self.gazetteer = load_gazetteer()

        self.danger_keywords = ["meningkat", "wabah", "meninggal", "klb", "darurat", "waspada"]

        self._regex_patterns = [
            r"\b(?:di|dari)\s+(kota|kabupaten|kab\.|kab|provinsi|prov\.)\s+([A-Z][\w\.\-]+(?:\s+[A-Z][\w\.\-]+){0,4})",
            r"\b(?:di|dari)\s+([A-Z][\w\.\-]+(?:\s+[A-Z][\w\.\-]+){0,4})",
            r"\b(Kabupaten|Kab\.|Kota|Provinsi)\s+([A-Z][\w\.\-]+(?:\s+[A-Z][\w\.\-]+){0,4})",
        ]

        self.MIN_CONF_FOR_GEOCODE = 0.60
        self.MIN_CONF_FOR_MAP = 0.75
        self.ALLOW_PROVINCE_ON_MAP = True

    # =========================
    # Helpers
    # =========================
    def _sanitize_loc(self, loc: str) -> str:
        loc = (loc or "").strip()
        loc = re.sub(r"\s+", " ", loc)
        loc = re.sub(r"(,\s*indonesia)+\s*$", "", loc, flags=re.IGNORECASE).strip()
        loc = loc.strip(" ,")
        return loc

    def _build_queries(self, loc_query: str) -> List[str]:
        loc_query = self._sanitize_loc(loc_query)
        if not loc_query:
            return []

        parts = [p.strip() for p in loc_query.split(",") if p.strip()]
        queries = []

        queries.append(f"{loc_query}, Indonesia")

        if parts:
            first = parts[0]
            first2 = re.sub(
                r"^(kota|kabupaten|kab\.|kab|provinsi|prov\.)\s+",
                "",
                first,
                flags=re.IGNORECASE
            ).strip()
            if first2 and first2.lower() != first.lower():
                rest = ", ".join([first2] + parts[1:])
                queries.append(f"{rest}, Indonesia")

        if parts:
            main = parts[0]
            main2 = re.sub(
                r"^(kota|kabupaten|kab\.|kab|provinsi|prov\.)\s+",
                "",
                main,
                flags=re.IGNORECASE
            ).strip()
            if main2:
                queries.append(f"{main2}, Indonesia")

        out = []
        seen = set()
        for q in queries:
            k = q.lower()
            if k not in seen:
                out.append(q)
                seen.add(k)

        return out

    def _split_admin_from_match(self, entry: Dict) -> Tuple[str, str]:
        level = (entry.get("level") or "").lower().strip()

        admin_province = clean_text(entry.get("admin_province", ""))
        admin_kabkota = clean_text(entry.get("admin_kabkota", ""))

        if level in ["province", "provinsi"]:
            return admin_province or entry.get("name", ""), ""

        if level in ["kota", "kabupaten", "city", "regency"]:
            return admin_province, admin_kabkota or entry.get("name", "")

        return admin_province, admin_kabkota

    # =========================
    # URL resolve
    # =========================
    def resolve_final_url(self, url: str) -> str:
        if not url:
            return ""
        try:
            r = self.session.get(url, timeout=self.fetch_timeout, allow_redirects=True)
            return r.url or url
        except Exception:
            return url

    # =========================
    # Scrape article body
    # =========================
    def get_article_text(self, url: str) -> str:
        if not url:
            return ""
        if url in self._article_cache:
            return self._article_cache[url]

        try:
            r = self.session.get(url, timeout=self.fetch_timeout, allow_redirects=True)
            if r.status_code != 200 or not r.text:
                self._article_cache[url] = ""
                return ""

            soup = BeautifulSoup(r.text, "lxml")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()

            article = soup.find("article")
            scope = article if article else soup
            ps = scope.find_all("p")

            text = " ".join([p.get_text(" ", strip=True) for p in ps])
            text = clean_text(text)
            text = truncate(text, 6000)

            self._article_cache[url] = text
            return text
        except Exception:
            self._article_cache[url] = ""
            return ""

    # =========================
    # Scoring
    # =========================
    def score(self, title: str, body: str = "", event_types: List[str] | None = None, severity_nlp: int = 0) -> int:
        base = 20
        t = ((title or "") + " " + (body or "")).lower()

        for kw in self.danger_keywords:
            if kw in t:
                base += 15

        if event_types:
            if "outbreak" in event_types:
                base += 20
            if "death" in event_types:
                base += 20
            if "increase" in event_types:
                base += 10
            if "alert" in event_types:
                base += 10

        return min(max(base, severity_nlp or 0), 100)

    # =========================
    # Extract location
    # =========================
    def match_location(self, text: str) -> Tuple[str, str, float, str, str]:
        t_clean = clean_text(text)
        t_norm = norm(t_clean)

        best = None
        best_len = 0
        level_prio = {
            "kota": 4,
            "city": 4,
            "kabupaten": 3,
            "regency": 3,
            "province": 2,
            "provinsi": 2,
        }

        entities = extract_entities(t_clean)
        candidate_locs = entities.get("locations", [])

        # 1) coba kandidat lokasi dari NLP dulu
        for candidate in candidate_locs:
            c_norm = norm(candidate)
            if not c_norm:
                continue

            for e in self.gazetteer:
                for form in e["forms_norm"]:
                    if c_norm == form or contains_phrase(c_norm, form) or contains_phrase(form, c_norm):
                        this_len = len(form)
                        if best is None:
                            best, best_len = e, this_len
                        else:
                            if (
                                (level_prio.get(e["level"], 0) > level_prio.get(best["level"], 0))
                                or (
                                    level_prio.get(e["level"], 0) == level_prio.get(best["level"], 0)
                                    and this_len > best_len
                                )
                            ):
                                best, best_len = e, this_len

        # 2) fallback ke full text
        if best is None:
            for e in self.gazetteer:
                for form in e["forms_norm"]:
                    if contains_phrase(t_norm, form):
                        this_len = len(form)
                        if best is None:
                            best, best_len = e, this_len
                        else:
                            if (
                                (level_prio.get(e["level"], 0) > level_prio.get(best["level"], 0))
                                or (
                                    level_prio.get(e["level"], 0) == level_prio.get(best["level"], 0)
                                    and this_len > best_len
                                )
                            ):
                                best, best_len = e, this_len

        if best:
            level = best["level"]
            conf = 0.90 if level in ["kota", "kabupaten", "city", "regency"] else 0.75

            admin_province, admin_kabkota = self._split_admin_from_match(best)

            loc = best["name"]
            if admin_kabkota and admin_province:
                loc = f"{admin_kabkota}, {admin_province}"
            elif admin_province:
                loc = admin_province

            return (
                self._sanitize_loc(loc),
                level,
                conf,
                self._sanitize_loc(admin_province),
                self._sanitize_loc(admin_kabkota),
            )

        # regex fallback
        loc_rx = self.extract_location_regex(t_clean)
        if loc_rx:
            skip, _ = validate_location(loc_rx)
            if not skip:
                return self._sanitize_loc(loc_rx), "unknown", 0.55, "", ""

        return "", "none", 0.0, "", ""

    def extract_location_regex(self, text: str) -> str:
        t = clean_text(text)
        for pat in self._regex_patterns:
            m = re.search(pat, t)
            if m:
                cand = m.group(m.lastindex).strip()
                cand = cand.strip(" ,.-;:()[]{}\"'")
                cand = re.sub(r"\s+", " ", cand).strip()
                if len(cand) >= 3:
                    cand = re.split(
                        r"\b(Tahun|Hari|Pekan|Minggu|Bulan|Senin|Selasa|Rabu|Kamis|Jumat|Sabtu|Minggu)\b",
                        cand
                    )[0].strip()
                    return cand
        return ""

    # =========================
    # Geocode (robust)
    # =========================
    def geocode_location(self, loc_query: str, conf: float) -> Tuple[str, str, str]:
        loc_query = self._sanitize_loc(loc_query)

        skip, reason = validate_location(loc_query)
        if skip:
            return "", "", reason

        if conf < self.MIN_CONF_FOR_GEOCODE:
            return "", "", "SKIP_LOW_CONF"

        cache_key = loc_query.lower().strip()
        if cache_key in self._geocode_cache:
            return self._geocode_cache[cache_key]

        queries = self._build_queries(loc_query)
        if not queries:
            self._geocode_cache[cache_key] = ("", "", "EMPTY_LOC")
            return "", "", "EMPTY_LOC"

        last_err = "NOT_FOUND"

        for q in queries:
            for attempt in range(3):
                try:
                    res = self.geocode(q, exactly_one=True, addressdetails=False)

                    if res is None:
                        last_err = "NOT_FOUND"
                        break

                    lat = str(res.latitude)
                    lon = str(res.longitude)

                    self._geocode_cache[cache_key] = (lat, lon, "OK")
                    return lat, lon, "OK"

                except GeocoderQuotaExceeded:
                    last_err = "RATE_LIMIT"
                    time.sleep(2.5 * (attempt + 1))
                except GeocoderTimedOut:
                    last_err = "TIMEOUT"
                    time.sleep(1.5 * (attempt + 1))
                except GeocoderUnavailable:
                    last_err = "NET_ERR"
                    time.sleep(2.0 * (attempt + 1))
                except GeocoderServiceError:
                    last_err = "SERVICE_ERR"
                    time.sleep(2.0 * (attempt + 1))
                except socket.gaierror:
                    last_err = "NET_ERR"
                    time.sleep(2.0 * (attempt + 1))
                except Exception:
                    last_err = "NET_ERR"
                    time.sleep(2.0 * (attempt + 1))

        self._geocode_cache[cache_key] = ("", "", last_err)
        return "", "", last_err

    # =========================
    # Main
    # =========================
    def run(self) -> List[Dict]:
        rows: List[Dict] = []
        seen = set()

        for kw in self.keywords:
            print(f"🔎 Scanning {kw}...")
            feed = feedparser.parse(self.rss_url.format(query=kw.replace(" ", "+")))

            for entry in getattr(feed, "entries", []):
                title = truncate(clean_text(getattr(entry, "title", "")), 220)
                summary = truncate(clean_text(getattr(entry, "summary", "")), 320)
                link = clean_text(getattr(entry, "link", ""))
                tanggal = clean_text(getattr(entry, "published", ""))

                sumber = "Google News"
                if hasattr(entry, "source") and hasattr(entry.source, "title"):
                    sumber = clean_text(entry.source.title)

                if (title, link) in seen:
                    continue
                seen.add((title, link))

                final_url = self.resolve_final_url(link)
                body = self.get_article_text(final_url)
                combined = f"{title} {summary} {body}"

                entities = extract_entities(combined)
                detected_diseases = entities.get("diseases", [])
                event_types = entities.get("event_types", [])
                severity_nlp = entities.get("severity", 0)

                skor = self.score(
                    title,
                    body=combined,
                    event_types=event_types,
                    severity_nlp=severity_nlp
                )

                lokasi, level, conf, admin_province, admin_kabkota = self.match_location(combined)

                loc_for_geocode = lokasi
                if admin_kabkota and admin_province:
                    loc_for_geocode = f"{admin_kabkota}, {admin_province}"
                elif admin_province and not lokasi:
                    loc_for_geocode = admin_province

                lat, lon, geo_status = self.geocode_location(loc_for_geocode, conf)

                rows.append({
                    "body": body,
                    "combined_text": combined,
                    "tanggal": tanggal,
                    "penyakit_tag": kw,
                    "detected_diseases": "|".join(detected_diseases),
                    "event_types": "|".join(event_types),
                    "severity_nlp": severity_nlp,
                    "skor_ancaman": skor,
                    "lokasi_mentah": lokasi,
                    "level_lokasi": level,
                    "confidence_lokasi": conf,
                    "admin_province": admin_province,
                    "admin_kabkota": admin_kabkota,
                    "geocode_status": geo_status,
                    "judul": title,
                    "sumber": sumber,
                    "link": link,
                    "final_url": final_url,
                    "summary": summary,
                    "lat": lat,
                    "lon": lon,
                })

                time.sleep(0.2)

        return rows


# =========================
# CSV OUTPUT
# =========================
def write_csv(path: str, rows: List[Dict], fieldnames: List[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            out = {k: clean_text(r.get(k, "")) for k in fieldnames}
            w.writerow(out)


if __name__ == "__main__":
    crawler = MedIntelCrawler()
    rows = crawler.run()

    fields = [
        "tanggal",
        "penyakit_tag",
        "detected_diseases",
        "event_types",
        "severity_nlp",
        "skor_ancaman",
        "lokasi_mentah",
        "level_lokasi",
        "confidence_lokasi",
        "admin_province",
        "admin_kabkota",
        "geocode_status",
        "judul",
        "sumber",
        "link",
        "final_url",
        "summary",
        "lat",
        "lon",
    ]

    write_csv("output/data_intel_raw.csv", rows, fields)

    geo_rows = []
    for r in rows:
        if r.get("geocode_status") != "OK":
            continue

        try:
            conf = float(r.get("confidence_lokasi", 0) or 0)
        except Exception:
            conf = 0.0

        level = (r.get("level_lokasi") or "").strip().lower()

        if conf < crawler.MIN_CONF_FOR_MAP:
            continue

        if crawler.ALLOW_PROVINCE_ON_MAP:
            if level not in ["kota", "kabupaten", "city", "regency", "province", "provinsi"]:
                continue
        else:
            if level not in ["kota", "kabupaten", "city", "regency"]:
                continue

        if not r.get("lat") or not r.get("lon"):
            continue

        geo_rows.append(r)

    write_csv("output/data_intel_geo.csv", geo_rows, fields)

    print(f"✅ RAW: {len(rows)} | GEO (mapped): {len(geo_rows)}")