import re
from dataclasses import dataclass
from typing import Optional

from intel.models import Location, LocationAlias


@dataclass
class LocationResolutionResult:
    location_obj: Optional[Location]
    confidence: float = 0.0
    method: str = ""
    note: str = ""


PROVINCE_ALIASES = {
    "jabar": "jawa_barat",
    "jateng": "jawa_tengah",
    "jatim": "jawa_timur",
    "dki": "dki_jakarta",
    "jakarta": "dki_jakarta",
    "diy": "di_yogyakarta",
    "yogyakarta": "di_yogyakarta",
    "sumut": "sumatera_utara",
    "sumbar": "sumatera_barat",
    "sumsel": "sumatera_selatan",
    "kepri": "kepulauan_riau",
    "babel": "bangka_belitung",
    "kalbar": "kalimantan_barat",
    "kalteng": "kalimantan_tengah",
    "kalsel": "kalimantan_selatan",
    "kaltim": "kalimantan_timur",
    "kalut": "kalimantan_utara",
    "sulut": "sulawesi_utara",
    "sulteng": "sulawesi_tengah",
    "sulsel": "sulawesi_selatan",
    "sultra": "sulawesi_tenggara",
    "sulbar": "sulawesi_barat",
    "malut": "maluku_utara",
    "ntb": "nusa_tenggara_barat",
    "ntt": "nusa_tenggara_timur",
}


def normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("&", " dan ")
    value = re.sub(r"\bprovinsi\b", "", value)
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def strip_admin_prefix(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"^\s*kab\.\s*", "", value)
    value = re.sub(r"^\s*kabupaten\s*", "", value)
    value = re.sub(r"^\s*kota\s*", "", value)
    return value.strip()


def resolve_location_from_text(raw_location: str) -> LocationResolutionResult:
    if not raw_location or not raw_location.strip():
        return LocationResolutionResult(
            location_obj=None,
            confidence=0.0,
            method="empty",
            note="raw_location_text empty",
        )

    raw_location = raw_location.strip()
    norm = normalize_text(raw_location)

    # 1) Province alias map
    province_alias = PROVINCE_ALIASES.get(norm)
    if province_alias:
        province = Location.objects.filter(
            level="province",
            province_code=province_alias,
            is_active=True,
        ).first()
        if province:
            return LocationResolutionResult(
                location_obj=province,
                confidence=0.95,
                method="province_alias",
                note="matched by province alias",
            )

    # 2) Direct exact Location match
    location = Location.objects.filter(
        normalized_name__in={norm, norm.replace("_", " "), norm.replace(" ", "_")},
        is_active=True,
        is_false_positive=False,
    ).first()
    if location:
        return LocationResolutionResult(
            location_obj=location,
            confidence=0.95,
            method="location_exact",
            note="matched by normalized_name",
        )

    # 3) Alias match
    alias = (
        LocationAlias.objects.select_related("location")
        .filter(
            normalized_alias__in={norm, norm.replace("_", " "), norm.replace(" ", "_")},
            is_active=True,
            location__is_active=True,
            location__is_false_positive=False,
        )
        .first()
    )
    if alias:
        return LocationResolutionResult(
            location_obj=alias.location,
            confidence=0.93,
            method="alias_exact",
            note=f"matched by alias: {alias.alias}",
        )

    # 4) Explicit kota/kabupaten
    lowered = raw_location.lower()
    explicit_level = None
    base_name = None

    if lowered.startswith("kota "):
        explicit_level = "city"
        base_name = strip_admin_prefix(raw_location)
    elif lowered.startswith("kab ") or lowered.startswith("kab. ") or lowered.startswith("kabupaten "):
        explicit_level = "regency"
        base_name = strip_admin_prefix(raw_location)

    if explicit_level and base_name:
        base_norm = normalize_text(base_name)

        # try by location normalized_name
        candidate = Location.objects.filter(
            level=explicit_level,
            normalized_name__in={base_norm, normalize_text(raw_location)},
            is_active=True,
            is_false_positive=False,
        ).first()

        if candidate:
            return LocationResolutionResult(
                location_obj=candidate,
                confidence=0.90,
                method="explicit_admin_prefix",
                note=f"matched explicit {explicit_level}",
            )

        # try by alias normalized_alias
        alias = (
            LocationAlias.objects.select_related("location")
            .filter(
                normalized_alias=base_norm,
                location__level=explicit_level,
                is_active=True,
                location__is_active=True,
                location__is_false_positive=False,
            )
            .first()
        )
        if alias:
            return LocationResolutionResult(
                location_obj=alias.location,
                confidence=0.89,
                method="explicit_admin_alias",
                note=f"matched explicit alias {explicit_level}",
            )

    # 5) Generic city/regency match
    generic_norm = normalize_text(strip_admin_prefix(raw_location))
    candidates = list(
        Location.objects.filter(
            level__in=["city", "regency"],
            normalized_name=generic_norm,
            is_active=True,
            is_false_positive=False,
        )[:10]
    )

    if len(candidates) == 1:
        return LocationResolutionResult(
            location_obj=candidates[0],
            confidence=0.80,
            method="generic_unique",
            note="unique city/regency match",
        )

    # 6) Generic alias match
    alias_candidates = list(
        LocationAlias.objects.select_related("location").filter(
            normalized_alias=generic_norm,
            is_active=True,
            location__level__in=["city", "regency"],
            location__is_active=True,
            location__is_false_positive=False,
        )[:10]
    )
    unique_location_ids = {a.location_id for a in alias_candidates}
    if len(unique_location_ids) == 1 and alias_candidates:
        return LocationResolutionResult(
            location_obj=alias_candidates[0].location,
            confidence=0.78,
            method="generic_alias_unique",
            note="unique alias city/regency match",
        )

    if len(candidates) > 1 or len(unique_location_ids) > 1:
        return LocationResolutionResult(
            location_obj=None,
            confidence=0.20,
            method="ambiguous",
            note=f"multiple candidates for {raw_location}",
        )

    return LocationResolutionResult(
        location_obj=None,
        confidence=0.0,
        method="not_found",
        note=f"no match for {raw_location}",
    )