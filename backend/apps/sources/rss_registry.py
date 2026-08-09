"""Kandidat endpoint RSS/Atom resmi untuk Source Master MedIntel.

Daftar ini hanya berisi endpoint milik penerbit/instansi. Keberadaan URL di
registry bukan bukti bahwa feed masih aktif. Command ``activate_official_rss``
selalu mengunduh dan memvalidasi feed beserta URL artikelnya sebelum membuat
``SourceSeedUrl``.
"""

from __future__ import annotations


OFFICIAL_RSS_CANDIDATES: dict[str, tuple[str, ...]] = {
    # Instansi dan organisasi kesehatan.
    "kemkes": (
        "https://kemkes.go.id/id/rss/article/rilis-berita",
        "https://kemkes.go.id/id/rss/article/artikel-kesehatan",
    ),
    "africa-cdc": (
        "https://africacdc.org/feed/",
    ),
    "cdc-us": (
        "https://wwwnc.cdc.gov/eid/rss/expedited.xml",
        "https://wwwnc.cdc.gov/eid/rss/ahead-of-print.xml",
    ),
    "ecdc": (
        "https://www.ecdc.europa.eu/en/taxonomy/term/1505/feed",
        "https://www.ecdc.europa.eu/en/taxonomy/term/1295/feed",
        "https://www.ecdc.europa.eu/en/taxonomy/term/1307/feed",
    ),
    "fao": (
        "https://www.fao.org/newsroom/rss/en",
    ),
    "reliefweb": (
        "https://reliefweb.int/updates/rss.xml",
    ),
    "ukhsa": (
        "https://ukhsa.blog.gov.uk/feed/",
    ),
    "unicef-indonesia": (
        "https://www.unicef.org/indonesia/rss.xml",
    ),
    "who": (
        "https://www.afro.who.int/rss/emergencies.xml",
    ),
    "woah": (
        "https://www.woah.org/en/feed/",
    ),

    # Media internasional.
    "al-jazeera": (
        "https://www.aljazeera.com/xml/rss/all.xml",
    ),
    "bbc": (
        "https://www.bbc.com/news/rss.xml",
    ),
    "cna": (
        "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml",
    ),
    "dw": (
        "https://rss.dw.com/rdf/rss-en-all",
    ),
    "france24": (
        "https://www.france24.com/en/rss",
    ),
    "guardian": (
        "https://www.theguardian.com/world/rss",
    ),
    "inquirer-ph": (
        "https://newsinfo.inquirer.net/feed",
    ),
    "japan-times": (
        "https://www.japantimes.co.jp/feed/",
    ),
    "nhk": (
        "https://www3.nhk.or.jp/rss/news/cat0.xml",
    ),
    "rappler": (
        "https://www.rappler.com/feed/",
    ),
    "scmp": (
        "https://www.scmp.com/rss/91/feed",
    ),
    "straits-times": (
        "https://www.straitstimes.com/news/world/rss.xml",
    ),
    "the-hindu": (
        "https://www.thehindu.com/news/national/feeder/default.rss",
    ),
    "the-star-my": (
        "https://www.thestar.com.my/rss/News/Nation",
    ),
    "vnexpress": (
        "https://vnexpress.net/rss/tin-moi-nhat.rss",
    ),

    # Media nasional Indonesia.
    "antara": (
        "https://www.antaranews.com/rss/terkini.xml",
        "https://www.antaranews.com/rss/humaniora.xml",
    ),
    "cnbc-indonesia": (
        "https://www.cnbcindonesia.com/news/rss",
    ),
    "cnn-indonesia": (
        "https://www.cnnindonesia.com/nasional/rss",
    ),
    "detik": (
        "https://news.detik.com/rss",
    ),
    "detik-health": (
        "https://health.detik.com/rss",
    ),
    "jawa-pos": (
        "https://www.jawapos.com/nasional/rss",
    ),
    "katadata": (
        "https://katadata.co.id/rss",
    ),
    "kontan": (
        "https://rss.kontan.co.id/news/nasional",
    ),
    "kumparan": (
        "https://lapi.kumparan.com/v2.0/rss/",
    ),
    "liputan6": (
        "https://feed.liputan6.com/rss/news",
        "https://www.liputan6.com/rss",
    ),
    "liputan6-health": (
        "https://www.liputan6.com/health/rss",
    ),
    "media-indonesia": (
        "https://mediaindonesia.com/feed",
    ),
    "mongabay-id": (
        "https://www.mongabay.co.id/feed/",
    ),
    "pikiran-rakyat": (
        "https://www.pikiran-rakyat.com/rss",
    ),
    "republika": (
        "https://www.republika.co.id/rss/nasional/",
    ),
    "rri": (
        "https://rri.co.id/rss",
    ),
    "suara": (
        "https://www.suara.com/rss/news",
    ),
    "tempo": (
        "https://rss.tempo.co/nasional",
    ),
    "tribunnews": (
        "https://www.tribunnews.com/rss",
    ),
    "voa-indonesia": (
        "https://www.voaindonesia.com/api/zmgqoe$mvi",
    ),

    # Media lokal/regional yang sudah ada pada Source Master.
    "bali-post": (
        "https://www.balipost.com/feed",
    ),
    "cenderawasih-pos": (
        "https://cenderawasihpos.jawapos.com/feed/",
    ),
    "fajar": (
        "https://fajar.co.id/feed/",
    ),
    "kompas-bandung": (
        "https://bandung.kompas.com/rss",
    ),
    "suara-merdeka": (
        "https://www.suaramerdeka.com/rss",
    ),
    "surya": (
        "https://surabaya.tribunnews.com/rss",
    ),
    "tribun-jabar": (
        "https://jabar.tribunnews.com/rss",
    ),
    "tribun-jateng": (
        "https://jateng.tribunnews.com/rss",
    ),
    "tribun-jatim": (
        "https://jatim.tribunnews.com/rss",
    ),
    "tribun-timur": (
        "https://makassar.tribunnews.com/rss",
    ),
}


KNOWN_WITHOUT_SAFE_OFFICIAL_RSS: dict[str, str] = {
    "associated-press": (
        "Tidak ada endpoint RSS publik resmi yang dapat divalidasi."
    ),
    "gisaid": (
        "Layanan berorientasi basis data dan akses akun, bukan feed artikel."
    ),
    "promed": (
        "Distribusi alert saat ini berbasis langganan; feed publik tidak "
        "diasumsikan tersedia."
    ),
    "reuters": (
        "Endpoint RSS berita Reuters lama tidak lagi diperlakukan aktif."
    ),
}
