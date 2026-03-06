# # crawler.py
# import re
# import time
# import html
# import csv
# import socket
# from typing import Dict, List, Tuple
# from nlp_extractor import extract_entities

# import feedparser
# import requests
# from bs4 import BeautifulSoup

# from geopy.geocoders import Nominatim
# from geopy.extra.rate_limiter import RateLimiter
# from geopy.exc import (
#     GeocoderTimedOut,
#     GeocoderUnavailable,
#     GeocoderServiceError,
#     GeocoderQuotaExceeded,
# )

# # =========================
# # UTIL
# # =========================
# def clean_text(x: str) -> str:
#     """Remove html tags, decode entities, normalize whitespace."""
#     if x is None:
#         return ""
#     x = html.unescape(str(x))
#     x = re.sub(r"<[^>]+>", " ", x)
#     x = re.sub(r"\s+", " ", x).strip()
#     return x


# def truncate(s: str, n: int) -> str:
#     s = s or ""
#     return s if len(s) <= n else s[:n].rstrip() + "..."


# def norm(s: str) -> str:
#     """Normalize for matching: lowercase, remove punctuation, collapse spaces."""
#     s = (s or "").lower()
#     s = re.sub(r"[^a-z0-9\s\-]", " ", s)
#     s = re.sub(r"\s+", " ", s).strip()
#     return s


# def contains_phrase(text_norm: str, phrase_norm: str) -> bool:
#     """Word-boundary phrase match."""
#     if not phrase_norm:
#         return False
#     return re.search(rf"\b{re.escape(phrase_norm)}\b", text_norm) is not None


# # =========================
# # GAZETTEER
# # =========================
# def load_gazetteer(path: str = "gazetteer_id.csv") -> List[Dict]:
#     """
#     Expected CSV columns:
#       name, level (province/kabupaten/kota), province, aliases (a|b|c)
#     """
#     entries: List[Dict] = []
#     with open(path, "r", encoding="utf-8") as f:
#         reader = csv.DictReader(f)
#         for r in reader:
#             name = clean_text(r.get("name", ""))
#             level = clean_text(r.get("level", ""))
#             prov = clean_text(r.get("province", ""))

#             aliases_raw = clean_text(r.get("aliases", ""))
#             aliases = [a.strip() for a in aliases_raw.split("|") if a.strip()]

#             forms = [name] + aliases
#             forms_norm = list({norm(x) for x in forms if norm(x)})

#             entries.append({
#                 "name": name,
#                 "level": level,          # kota/kabupaten/province
#                 "province": prov,        # parent province for kota/kabupaten
#                 "forms_norm": forms_norm
#             })

#     prio = {"kota": 3, "kabupaten": 2, "province": 1}
#     entries.sort(key=lambda e: prio.get(e["level"], 0), reverse=True)
#     return entries


# # =========================
# # LOCATION VALIDATION
# # =========================
# def validate_location(loc: str) -> Tuple[bool, str]:
#     """
#     Returns: (should_skip, status_reason)
#     status_reason: EMPTY_LOC, SKIP_TOO_GENERAL, SKIP_NOISE
#     """
#     loc = (loc or "").strip()
#     if not loc:
#         return True, "EMPTY_LOC"

#     low = loc.lower().strip()

#     too_general_exact = {
#         "indonesia", "ri", "nusantara", "nasional", "global", "dunia",
#         "asia", "eropa", "amerika", "afrika", "australia"
#     }
#     if low in too_general_exact:
#         return True, "SKIP_TOO_GENERAL"

#     junk_exact = {
#         "ini", "awal", "video", "update", "terbaru", "hari ini", "kemarin",
#         "kemenkes", "kementerian", "pemerintah", "id", "co", "com"
#     }
#     if low in junk_exact:
#         return True, "SKIP_NOISE"

#     if len(low) < 4:
#         return True, "SKIP_NOISE"

#     bad_contains = ["kasus", "wabah", "meningkat", "waspada", "darurat", "kematian", "lonjakan", "suspek"]
#     if any(b in low for b in bad_contains):
#         return True, "SKIP_NOISE"

#     if low.count("indonesia") >= 2:
#         return True, "SKIP_TOO_GENERAL"

#     return False, ""


# # =========================
# # CRAWLER
# # =========================
# class MedIntelCrawler:
#     def __init__(self):
#         self.keywords = ["DBD", "Mpox", "Flu Burung", "Antraks", "KLB Penyakit", "Campak", "Rubela", "Diare", "Kolera", "Demam Tifoid", "Chikungunya", "Superflu", "Avian Influenza", "Nipah", "TBC", "HIV", "AIDS", "IMS", "Infeksi Menular Seksual", "Polio", "Difteri", "Batuk Renjan", "Pertusis", "Rabies", "Leptospirosis", "Antraks"]
#         self.rss_url = "https://news.google.com/rss/search?q={query}+when:7d&hl=id&gl=ID&ceid=ID:id"

#         self.session = requests.Session()
#         self.session.headers.update({
#             "User-Agent": "Mozilla/5.0 (MedIntel-ID; +https://example.local)"
#         })
#         self.fetch_timeout = 12

#         self.geolocator = Nominatim(user_agent="medintel-id-crawler")
#         self.geocode = RateLimiter(self.geolocator.geocode, min_delay_seconds=1, return_value_on_exception=None)

#         self._article_cache: Dict[str, str] = {}
#         self._geocode_cache: Dict[str, Tuple[str, str, str]] = {}

#         self.gazetteer = load_gazetteer("gazetteer_id.csv")

#         self.danger_keywords = ["meningkat", "wabah", "meninggal", "klb", "darurat", "waspada"]

#         self._regex_patterns = [
#             r"\b(?:di|dari)\s+(kota|kabupaten|kab\.|kab|provinsi|prov\.)\s+([A-Z][\w\.\-]+(?:\s+[A-Z][\w\.\-]+){0,4})",
#             r"\b(?:di|dari)\s+([A-Z][\w\.\-]+(?:\s+[A-Z][\w\.\-]+){0,4})",
#             r"\b(Kabupaten|Kab\.|Kota|Provinsi)\s+([A-Z][\w\.\-]+(?:\s+[A-Z][\w\.\-]+){0,4})",
#         ]

#         # Thresholds
#         self.MIN_CONF_FOR_GEOCODE = 0.60
#         self.MIN_CONF_FOR_MAP = 0.75
#         self.ALLOW_PROVINCE_ON_MAP = True  # <- supaya tidak 0 dulu, bisa kamu matikan jika gazetteer sudah kaya

#     # =========================
#     # Helpers
#     # =========================
#     def _sanitize_loc(self, loc: str) -> str:
#         loc = (loc or "").strip()
#         loc = re.sub(r"\s+", " ", loc)
#         loc = re.sub(r"(,\s*indonesia)+\s*$", "", loc, flags=re.IGNORECASE).strip()
#         loc = loc.strip(" ,")
#         return loc

#     def _build_queries(self, loc_query: str) -> List[str]:
#         """
#         Build fallback geocode queries (progressive relaxation).
#         """
#         loc_query = self._sanitize_loc(loc_query)
#         if not loc_query:
#             return []

#         parts = [p.strip() for p in loc_query.split(",") if p.strip()]
#         queries = []

#         # main
#         queries.append(f"{loc_query}, Indonesia")

#         # fallback 1: remove admin prefix on first component
#         if parts:
#             first = parts[0]
#             first2 = re.sub(
#                 r"^(kota|kabupaten|kab\.|kab|provinsi|prov\.)\s+",
#                 "",
#                 first,
#                 flags=re.IGNORECASE
#             ).strip()
#             if first2 and first2.lower() != first.lower():
#                 rest = ", ".join([first2] + parts[1:])
#                 queries.append(f"{rest}, Indonesia")

#         # fallback 2: only the main component
#         if parts:
#             main = parts[0]
#             main2 = re.sub(
#                 r"^(kota|kabupaten|kab\.|kab|provinsi|prov\.)\s+",
#                 "",
#                 main,
#                 flags=re.IGNORECASE
#             ).strip()
#             if main2:
#                 queries.append(f"{main2}, Indonesia")

#         # dedup preserve order
#         out = []
#         seen = set()
#         for q in queries:
#             k = q.lower()
#             if k not in seen:
#                 out.append(q)
#                 seen.add(k)

#         return out

#     # =========================
#     # URL resolve
#     # =========================
#     def resolve_final_url(self, url: str) -> str:
#         if not url:
#             return ""
#         try:
#             r = self.session.get(url, timeout=self.fetch_timeout, allow_redirects=True)
#             return r.url or url
#         except Exception:
#             return url

#     # =========================
#     # Scrape article body
#     # =========================
#     def get_article_text(self, url: str) -> str:
#         if not url:
#             return ""
#         if url in self._article_cache:
#             return self._article_cache[url]

#         try:
#             r = self.session.get(url, timeout=self.fetch_timeout, allow_redirects=True)
#             if r.status_code != 200 or not r.text:
#                 self._article_cache[url] = ""
#                 return ""

#             soup = BeautifulSoup(r.text, "lxml")
#             for tag in soup(["script", "style", "noscript"]):
#                 tag.decompose()

#             article = soup.find("article")
#             scope = article if article else soup
#             ps = scope.find_all("p")

#             text = " ".join([p.get_text(" ", strip=True) for p in ps])
#             text = clean_text(text)
#             text = truncate(text, 6000)

#             self._article_cache[url] = text
#             return text
#         except Exception:
#             self._article_cache[url] = ""
#             return ""

#     # =========================
#     # Scoring
#     # =========================
#     def score(self, title: str) -> int:
#         base = 20
#         t = (title or "").lower()
#         for kw in self.danger_keywords:
#             if kw in t:
#                 base += 15
#         return min(base, 100)

#     # =========================
#     # Extract location
#     # =========================
#     def match_location(self, text: str) -> Tuple[str, str, float]:
#         t_clean = clean_text(text)
#         t_norm = norm(t_clean)

#         best = None
#         best_len = 0
#         level_prio = {"kota": 3, "kabupaten": 2, "province": 1}

#         for e in self.gazetteer:
#             for form in e["forms_norm"]:
#                 if contains_phrase(t_norm, form):
#                     this_len = len(form)
#                     if best is None:
#                         best, best_len = e, this_len
#                     else:
#                         if (level_prio.get(e["level"], 0) > level_prio.get(best["level"], 0)) or (this_len > best_len):
#                             best, best_len = e, this_len

#         if best:
#             level = best["level"]
#             conf = 0.90 if level in ["kota", "kabupaten"] else 0.75

#             loc = best["name"]
#             if level in ["kota", "kabupaten"] and best.get("province"):
#                 loc = f"{loc}, {best['province']}"
#             return self._sanitize_loc(loc), level, conf

#         # regex fallback (low conf)
#         loc_rx = self.extract_location_regex(t_clean)
#         if loc_rx:
#             skip, _ = validate_location(loc_rx)
#             if not skip:
#                 return self._sanitize_loc(loc_rx), "unknown", 0.55

#         return "", "none", 0.0

#     def extract_location_regex(self, text: str) -> str:
#         t = clean_text(text)
#         for pat in self._regex_patterns:
#             m = re.search(pat, t)
#             if m:
#                 cand = m.group(m.lastindex).strip()
#                 cand = cand.strip(" ,.-;:()[]{}\"'")
#                 cand = re.sub(r"\s+", " ", cand).strip()
#                 if len(cand) >= 3:
#                     cand = re.split(r"\b(Tahun|Hari|Pekan|Minggu|Bulan|Senin|Selasa|Rabu|Kamis|Jumat|Sabtu|Minggu)\b", cand)[0].strip()
#                     return cand
#         return ""

#     # =========================
#     # Geocode (robust)
#     # =========================
#     def geocode_location(self, loc_query: str, conf: float) -> Tuple[str, str, str]:
#         """
#         Returns (lat, lon, geocode_status)
#         geocode_status:
#           EMPTY_LOC, SKIP_TOO_GENERAL, SKIP_NOISE, SKIP_LOW_CONF,
#           OK, NOT_FOUND,
#           NET_ERR, TIMEOUT, RATE_LIMIT, SERVICE_ERR
#         """
#         loc_query = self._sanitize_loc(loc_query)

#         skip, reason = validate_location(loc_query)
#         if skip:
#             return "", "", reason

#         if conf < self.MIN_CONF_FOR_GEOCODE:
#             return "", "", "SKIP_LOW_CONF"

#         cache_key = loc_query.lower().strip()
#         if cache_key in self._geocode_cache:
#             return self._geocode_cache[cache_key]

#         queries = self._build_queries(loc_query)
#         if not queries:
#             self._geocode_cache[cache_key] = ("", "", "EMPTY_LOC")
#             return "", "", "EMPTY_LOC"

#         last_err = "NOT_FOUND"

#         for q in queries:
#             for attempt in range(3):
#                 try:
#                     res = self.geocode(q, exactly_one=True, addressdetails=False)

#                     if res is None:
#                         last_err = "NOT_FOUND"
#                         break

#                     lat = str(res.latitude)
#                     lon = str(res.longitude)

#                     self._geocode_cache[cache_key] = (lat, lon, "OK")
#                     return lat, lon, "OK"

#                 except GeocoderQuotaExceeded:
#                     last_err = "RATE_LIMIT"
#                     time.sleep(2.5 * (attempt + 1))
#                 except GeocoderTimedOut:
#                     last_err = "TIMEOUT"
#                     time.sleep(1.5 * (attempt + 1))
#                 except GeocoderUnavailable:
#                     last_err = "NET_ERR"
#                     time.sleep(2.0 * (attempt + 1))
#                 except GeocoderServiceError:
#                     last_err = "SERVICE_ERR"
#                     time.sleep(2.0 * (attempt + 1))
#                 except socket.gaierror:
#                     last_err = "NET_ERR"
#                     time.sleep(2.0 * (attempt + 1))
#                 except Exception:
#                     last_err = "NET_ERR"
#                     time.sleep(2.0 * (attempt + 1))

#         self._geocode_cache[cache_key] = ("", "", last_err)
#         return "", "", last_err

#     # =========================
#     # Main
#     # =========================
#     def run(self) -> List[Dict]:
#         rows: List[Dict] = []
#         seen = set()

#         for kw in self.keywords:
#             print(f"🔎 Scanning {kw}...")
#             feed = feedparser.parse(self.rss_url.format(query=kw.replace(" ", "+")))

#             for entry in getattr(feed, "entries", []):
#                 title = truncate(clean_text(getattr(entry, "title", "")), 220)
#                 summary = truncate(clean_text(getattr(entry, "summary", "")), 320)
#                 link = clean_text(getattr(entry, "link", ""))
#                 tanggal = clean_text(getattr(entry, "published", ""))

#                 sumber = "Google News"
#                 if hasattr(entry, "source") and hasattr(entry.source, "title"):
#                     sumber = clean_text(entry.source.title)

#                 if (title, link) in seen:
#                     continue
#                 seen.add((title, link))

#                 skor = self.score(title)

#                 final_url = self.resolve_final_url(link)
#                 body = self.get_article_text(final_url)

#                 combined = f"{title} {summary} {body}"

#                 lokasi, level, conf = self.match_location(combined)
#                 lat, lon, geo_status = self.geocode_location(lokasi, conf)

#                 rows.append({
#                     "tanggal": tanggal,
#                     "penyakit_tag": kw,
#                     "skor_ancaman": skor,
#                     "lokasi_mentah": lokasi,
#                     "level_lokasi": level,
#                     "confidence_lokasi": conf,
#                     "geocode_status": geo_status,
#                     "judul": title,
#                     "sumber": sumber,
#                     "link": link,
#                     "final_url": final_url,
#                     "summary": summary,
#                     "lat": lat,
#                     "lon": lon,
#                 })

#                 time.sleep(0.2)

#         return rows


# # =========================
# # CSV OUTPUT
# # =========================
# def write_csv(path: str, rows: List[Dict], fieldnames: List[str]) -> None:
#     with open(path, "w", newline="", encoding="utf-8") as f:
#         w = csv.DictWriter(f, fieldnames=fieldnames)
#         w.writeheader()
#         for r in rows:
#             out = {k: clean_text(r.get(k, "")) for k in fieldnames}
#             w.writerow(out)


# if __name__ == "__main__":
#     crawler = MedIntelCrawler()
#     rows = crawler.run()

#     fields = [
#         "tanggal",
#         "penyakit_tag",
#         "skor_ancaman",
#         "lokasi_mentah",
#         "level_lokasi",
#         "confidence_lokasi",
#         "geocode_status",
#         "judul",
#         "sumber",
#         "link",
#         "final_url",
#         "summary",
#         "lat",
#         "lon",
#     ]

#     # RAW: all rows
#     write_csv("output/data_intel_raw.csv", rows, fields)

#     # GEO: only "good enough" mapped points
#     geo_rows = []
#     for r in rows:
#         if r.get("geocode_status") != "OK":
#             continue

#         try:
#             conf = float(r.get("confidence_lokasi", 0) or 0)
#         except Exception:
#             conf = 0.0

#         level = (r.get("level_lokasi") or "").strip().lower()

#         # confidence threshold for mapping
#         if conf < crawler.MIN_CONF_FOR_MAP:
#             continue

#         # level filtering
#         if crawler.ALLOW_PROVINCE_ON_MAP:
#             if level not in ["kota", "kabupaten", "province"]:
#                 continue
#         else:
#             if level not in ["kota", "kabupaten"]:
#                 continue

#         if not r.get("lat") or not r.get("lon"):
#             continue

#         geo_rows.append(r)

#     write_csv("output/data_intel_geo.csv", geo_rows, fields)

#     print(f"✅ RAW: {len(rows)} | GEO (mapped): {len(geo_rows)}")
import re
import time
import html
import csv
import socket
from typing import Dict, List, Tuple

from nlp_extractor import extract_entities

import feedparser
import requests
from bs4 import BeautifulSoup

from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from geopy.exc import (
    GeocoderTimedOut,
    GeocoderUnavailable,
    GeocoderServiceError,
    GeocoderQuotaExceeded,
)


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


def norm(s: str) -> str:
    """Normalize for matching: lowercase, remove punctuation, collapse spaces."""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def contains_phrase(text_norm: str, phrase_norm: str) -> bool:
    """Word-boundary phrase match."""
    if not phrase_norm:
        return False
    return re.search(rf"\b{re.escape(phrase_norm)}\b", text_norm) is not None


# =========================
# GAZETTEER
# =========================
def load_gazetteer(path: str = "gazetteer_id.csv") -> List[Dict]:
    entries: List[Dict] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            name = clean_text(r.get("name", ""))
            level = clean_text(r.get("level", ""))
            prov = clean_text(r.get("province", ""))

            aliases_raw = clean_text(r.get("aliases", ""))
            aliases = [a.strip() for a in aliases_raw.split("|") if a.strip()]

            forms = [name] + aliases
            forms_norm = list({norm(x) for x in forms if norm(x)})

            entries.append({
                "name": name,
                "level": level,
                "province": prov,
                "forms_norm": forms_norm
            })

    prio = {"kota": 3, "kabupaten": 2, "province": 1}
    entries.sort(key=lambda e: prio.get(e["level"], 0), reverse=True)
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
        self.geocode = RateLimiter(self.geolocator.geocode, min_delay_seconds=1, return_value_on_exception=None)

        self._article_cache: Dict[str, str] = {}
        self._geocode_cache: Dict[str, Tuple[str, str, str]] = {}

        self.gazetteer = load_gazetteer("gazetteer_id.csv")

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
    def match_location(self, text: str) -> Tuple[str, str, float]:
        t_clean = clean_text(text)
        t_norm = norm(t_clean)

        best = None
        best_len = 0
        level_prio = {"kota": 3, "kabupaten": 2, "province": 1}

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
                            if (level_prio.get(e["level"], 0) > level_prio.get(best["level"], 0)) or (this_len > best_len):
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
                            if (level_prio.get(e["level"], 0) > level_prio.get(best["level"], 0)) or (this_len > best_len):
                                best, best_len = e, this_len

        if best:
            level = best["level"]
            conf = 0.90 if level in ["kota", "kabupaten"] else 0.75

            loc = best["name"]
            if level in ["kota", "kabupaten"] and best.get("province"):
                loc = f"{loc}, {best['province']}"
            return self._sanitize_loc(loc), level, conf

        # regex fallback
        loc_rx = self.extract_location_regex(t_clean)
        if loc_rx:
            skip, _ = validate_location(loc_rx)
            if not skip:
                return self._sanitize_loc(loc_rx), "unknown", 0.55

        return "", "none", 0.0

    def extract_location_regex(self, text: str) -> str:
        t = clean_text(text)
        for pat in self._regex_patterns:
            m = re.search(pat, t)
            if m:
                cand = m.group(m.lastindex).strip()
                cand = cand.strip(" ,.-;:()[]{}\"'")
                cand = re.sub(r"\s+", " ", cand).strip()
                if len(cand) >= 3:
                    cand = re.split(r"\b(Tahun|Hari|Pekan|Minggu|Bulan|Senin|Selasa|Rabu|Kamis|Jumat|Sabtu|Minggu)\b", cand)[0].strip()
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

                skor = self.score(title, body=combined, event_types=event_types, severity_nlp=severity_nlp)

                lokasi, level, conf = self.match_location(combined)
                lat, lon, geo_status = self.geocode_location(lokasi, conf)

                rows.append({
                    "tanggal": tanggal,
                    "penyakit_tag": kw,
                    "detected_diseases": "|".join(detected_diseases),
                    "event_types": "|".join(event_types),
                    "severity_nlp": severity_nlp,
                    "skor_ancaman": skor,
                    "lokasi_mentah": lokasi,
                    "level_lokasi": level,
                    "confidence_lokasi": conf,
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
            if level not in ["kota", "kabupaten", "province"]:
                continue
        else:
            if level not in ["kota", "kabupaten"]:
                continue

        if not r.get("lat") or not r.get("lon"):
            continue

        geo_rows.append(r)

    write_csv("output/data_intel_geo.csv", geo_rows, fields)

    print(f"✅ RAW: {len(rows)} | GEO (mapped): {len(geo_rows)}")