import re

from intel.models import DiseaseMaster, normalize_region_code


def normalize_disease_value(value):
    return normalize_region_code(value or "")


def _split_values(value):
    parts = re.split(r"[\n,;]+", value or "")
    return [part.strip() for part in parts if part.strip()]


def _candidate_values(disease):
    values = [disease.name]
    values.extend(_split_values(disease.aliases))
    values.extend(_split_values(disease.keyword_id))
    values.extend(_split_values(disease.keyword_en))
    return [value for value in values if value]


def _crawler_search_values(disease):
    values = []
    values.extend(_split_values(disease.aliases))
    values.extend(_split_values(disease.keyword_id))
    values.extend(_split_values(disease.keyword_en))

    # The formal SKDR label is a fallback only. Crawling should search the
    # disease subject/alias, not administrative labels such as "Malaria Konfirmasi".
    if not values:
        values.append(disease.name)

    return [value for value in values if value]


def build_disease_crawler_queries(limit_per_disease=4):
    """
    Build Google News search keywords from Disease Master.
    Each query keeps the canonical disease name so aliases do not fragment analytics.
    """
    rows = []
    seen_queries = set()

    for disease in DiseaseMaster.objects.filter(is_active=True).order_by("name"):
        candidates = _crawler_search_values(disease)

        picked = []
        for value in candidates:
            query = re.sub(r"\s+", " ", value or "").strip()
            if not query:
                continue
            query_key = query.lower()
            if query_key in seen_queries:
                continue
            seen_queries.add(query_key)
            picked.append(query)
            rows.append({
                "query": query,
                "disease_name": disease.name,
                "disease_id": disease.id,
                "alert_rule": disease.alert_rule,
                "severity_weight": disease.severity_weight,
                "skdr_priority": disease.skdr_priority,
                "report_24h": disease.report_24h,
                "emerging_watchlist": disease.emerging_watchlist,
                "reemerging_watch": disease.reemerging_watch,
            })
            if len(picked) >= limit_per_disease:
                break

    return rows


def match_disease_master(disease_tag="", title="", content=""):
    """
    Best-effort matcher for the existing disease_tag text.
    It keeps old features intact while allowing SKDR/emerging metadata to attach.
    """
    disease_tag = (disease_tag or "").strip()
    normalized_tag = normalize_disease_value(disease_tag)

    if normalized_tag:
        exact = DiseaseMaster.objects.filter(
            is_active=True,
            normalized_name=normalized_tag,
        ).first()
        if exact:
            return exact

    haystack = " ".join([disease_tag, title or "", content or ""]).lower()

    best_match = None
    best_score = 0

    for disease in DiseaseMaster.objects.filter(is_active=True).order_by("name"):
        for value in _candidate_values(disease):
            needle = value.lower().strip()
            if not needle:
                continue

            normalized_needle = normalize_disease_value(needle)
            if normalized_tag and normalized_tag == normalized_needle:
                return disease

            if needle in haystack:
                score = len(needle)
                if score > best_score:
                    best_match = disease
                    best_score = score

    return best_match


def disease_classification_flags(disease):
    if not disease:
        return {
            "skdr_priority": False,
            "report_24h": False,
            "emerging_watchlist": False,
            "reemerging_watch": False,
            "alert_rule": "",
            "disease_type": "",
            "severity_weight": "",
            "labels": [],
        }

    return {
        "skdr_priority": disease.skdr_priority,
        "report_24h": disease.report_24h,
        "emerging_watchlist": disease.emerging_watchlist,
        "reemerging_watch": disease.reemerging_watch,
        "alert_rule": disease.alert_rule,
        "disease_type": disease.disease_type,
        "severity_weight": disease.severity_weight,
        "labels": disease.classification_labels(),
    }


def sync_signal_disease_master(signal, save=True):
    disease = match_disease_master(
        disease_tag=getattr(signal, "disease_tag", "") or "",
        title=getattr(signal, "title", "") or "",
        content=getattr(signal, "content", "") or "",
    )

    if disease and getattr(signal, "disease_master_id", None) != disease.id:
        signal.disease_master = disease
        if save:
            signal.save(update_fields=["disease_master", "updated_at"])

    return disease
