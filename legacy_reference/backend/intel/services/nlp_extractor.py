import re
from typing import Dict, List

try:
    import spacy
except ImportError:
    spacy = None


def load_nlp():
    """
    Coba multilingual dulu, lalu English.
    Kalau gagal, tetap jalan pakai regex/dictionary.
    """
    if spacy is None:
        return None

    for model_name in ["xx_ent_wiki_sm", "en_core_web_sm"]:
        try:
            return spacy.load(model_name)
        except Exception:
            continue
    return None


NLP = load_nlp()

DISEASE_PATTERNS = {
    "DBD": ["dbd", "demam berdarah", "dengue"],
    "Mpox": ["mpox", "monkeypox"],
    "Flu Burung": ["flu burung", "avian influenza", "bird flu"],
    "Antraks": ["antraks", "anthrax"],
    "Campak": ["campak", "measles"],
    "Rubela": ["rubela", "rubella"],
    "Diare": ["diare", "diarrhea", "diarrhoea"],
    "Kolera": ["kolera", "cholera"],
    "Demam Tifoid": ["demam tifoid", "tifoid", "typhoid"],
    "Chikungunya": ["chikungunya"],
    "Superflu": ["superflu"],
    "Avian Influenza": ["avian influenza", "flu burung", "bird flu"],
    "Nipah": ["nipah"],
    "TBC": ["tbc", "tb", "tuberkulosis", "tuberculosis"],
    "HIV": ["hiv"],
    "AIDS": ["aids"],
    "IMS": ["ims", "infeksi menular seksual", "std", "sti"],
    "Polio": ["polio"],
    "Difteri": ["difteri", "diphtheria"],
    "Batuk Renjan": ["batuk renjan", "pertusis", "pertussis"],
    "Rabies": ["rabies"],
    "Leptospirosis": ["leptospirosis"],
    "KLB Penyakit": ["klb penyakit", "kejadian luar biasa"],
}

EVENT_PATTERNS = {
    "outbreak": ["wabah", "klb", "kejadian luar biasa", "merebak", "meluas"],
    "death": ["meninggal", "tewas", "kematian", "fatal"],
    "increase": ["meningkat", "naik", "lonjakan", "bertambah"],
    "alert": ["waspada", "darurat", "siaga", "peringatan"],
    "case_report": ["kasus", "terdeteksi", "ditemukan", "dilaporkan", "positif"],
}

SEVERITY_WEIGHTS = {
    "outbreak": 30,
    "death": 30,
    "increase": 20,
    "alert": 15,
    "case_report": 10,
}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def dedup_list(items: List[str]) -> List[str]:
    out = []
    seen = set()
    for x in items:
        key = (x or "").strip().lower()
        if key and key not in seen:
            out.append((x or "").strip())
            seen.add(key)
    return out


def extract_diseases(text: str) -> List[str]:
    text_low = (text or "").lower()
    found = []

    for disease_name, variants in DISEASE_PATTERNS.items():
        for v in variants:
            if v in text_low:
                found.append(disease_name)
                break

    return dedup_list(found)


def extract_event_types(text: str) -> List[str]:
    text_low = (text or "").lower()
    found = []

    for event_type, variants in EVENT_PATTERNS.items():
        for v in variants:
            if v in text_low:
                found.append(event_type)
                break

    return dedup_list(found)


def calculate_severity(event_types: List[str], text: str) -> int:
    score = 10
    for e in event_types:
        score += SEVERITY_WEIGHTS.get(e, 0)

    if re.search(r"\b\d+\b", text or ""):
        score += 5

    return min(score, 100)


def extract_locations_spacy(text: str) -> List[str]:
    if NLP is None:
        return []

    try:
        doc = NLP(text)
    except Exception:
        return []

    locations = [ent.text for ent in doc.ents if ent.label_ in ["GPE", "LOC"]]
    return dedup_list(locations)


def extract_locations_regex(text: str) -> List[str]:
    text = text or ""
    patterns = [
        r"\bdi\s+(Kota\s+[A-Z][\w\-]+(?:\s+[A-Z][\w\-]+){0,4})",
        r"\bdi\s+(Kabupaten\s+[A-Z][\w\-]+(?:\s+[A-Z][\w\-]+){0,4})",
        r"\bdi\s+([A-Z][\w\-]+(?:\s+[A-Z][\w\-]+){0,4})",
        r"\b(Kota\s+[A-Z][\w\-]+(?:\s+[A-Z][\w\-]+){0,4})",
        r"\b(Kabupaten\s+[A-Z][\w\-]+(?:\s+[A-Z][\w\-]+){0,4})",
        r"\b(Provinsi\s+[A-Z][\w\-]+(?:\s+[A-Z][\w\-]+){0,4})",
    ]

    out = []
    for pat in patterns:
        for m in re.finditer(pat, text):
            cand = m.group(1).strip(" ,.;:")
            if len(cand) >= 3:
                out.append(cand)

    return dedup_list(out)


def extract_entities(text: str) -> Dict:
    text = normalize_text(text)

    diseases = extract_diseases(text)
    event_types = extract_event_types(text)
    locations = dedup_list(extract_locations_spacy(text) + extract_locations_regex(text))
    severity = calculate_severity(event_types, text)

    return {
        "locations": locations,
        "diseases": diseases,
        "event_types": event_types,
        "severity": severity,
    }