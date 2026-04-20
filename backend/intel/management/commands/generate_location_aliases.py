from django.core.management.base import BaseCommand
from django.db import transaction

from intel.models import Location, LocationAlias
from intel.utils.location_resolver import normalize_text


PROVINCE_SHORTCUTS = {
    "Aceh": ["NAD"],
    "Sumatera Utara": ["Sumut", "North Sumatra"],
    "Sumatera Barat": ["Sumbar", "West Sumatra"],
    "Sumatera Selatan": ["Sumsel", "South Sumatra"],
    "Riau": [],
    "Kepulauan Riau": ["Kepri", "Kep. Riau", "Riau Islands"],
    "Jambi": [],
    "Bengkulu": [],
    "Lampung": [],
    "Bangka Belitung": ["Babel", "Kep. Bangka Belitung"],
    "Banten": [],
    "DKI Jakarta": ["DKI", "Jakarta"],
    "Jawa Barat": ["Jabar", "West Java"],
    "Jawa Tengah": ["Jateng", "Central Java"],
    "DI Yogyakarta": ["DIY", "Yogyakarta"],
    "Jawa Timur": ["Jatim", "East Java"],
    "Bali": [],
    "Nusa Tenggara Barat": ["NTB"],
    "Nusa Tenggara Timur": ["NTT"],
    "Kalimantan Barat": ["Kalbar"],
    "Kalimantan Tengah": ["Kalteng"],
    "Kalimantan Selatan": ["Kalsel"],
    "Kalimantan Timur": ["Kaltim"],
    "Kalimantan Utara": ["Kalut"],
    "Sulawesi Utara": ["Sulut"],
    "Sulawesi Tengah": ["Sulteng"],
    "Sulawesi Selatan": ["Sulsel"],
    "Sulawesi Tenggara": ["Sultra"],
    "Sulawesi Barat": ["Sulbar"],
    "Gorontalo": [],
    "Maluku": [],
    "Maluku Utara": ["Malut"],
    "Papua": [],
    "Papua Barat": ["Papbar"],
    "Papua Barat Daya": [],
    "Papua Selatan": [],
    "Papua Tengah": [],
    "Papua Pegunungan": [],
}


def add_alias(location, alias, created_counter):
    alias = (alias or "").strip()
    if not alias:
        return

    normalized_alias = normalize_text(alias)
    if not normalized_alias:
        return

    obj, created = LocationAlias.objects.get_or_create(
        location=location,
        normalized_alias=normalized_alias,
        defaults={
            "alias": alias,
            "is_primary": False,
            "is_active": True,
        },
    )
    if created:
        created_counter[0] += 1


class Command(BaseCommand):
    help = "Generate aliases for Location from existing master data"

    @transaction.atomic
    def handle(self, *args, **options):
        created_counter = [0]

        locations = Location.objects.filter(
            is_active=True,
            is_false_positive=False,
        ).select_related("parent")

        for loc in locations:
            display = (loc.display_name or loc.name or "").strip()
            name = (loc.name or "").strip()
            level = (loc.level or "").strip().lower()

            # primary-like aliases
            add_alias(loc, display, created_counter)
            if name and name.lower() != display.lower():
                add_alias(loc, name, created_counter)

            if level == "province":
                extras = PROVINCE_SHORTCUTS.get(display, [])
                for a in extras:
                    add_alias(loc, a, created_counter)

            elif level == "city":
                if display.lower().startswith("kota "):
                    bare = display[5:].strip()
                    add_alias(loc, bare, created_counter)
                    add_alias(loc, f"Kota {bare}", created_counter)

            elif level == "regency":
                low = display.lower()
                if low.startswith("kabupaten "):
                    bare = display[10:].strip()
                    add_alias(loc, bare, created_counter)
                    add_alias(loc, f"Kabupaten {bare}", created_counter)
                    add_alias(loc, f"Kab. {bare}", created_counter)
                    add_alias(loc, f"Kab {bare}", created_counter)
                elif low.startswith("kab. "):
                    bare = display[5:].strip()
                    add_alias(loc, bare, created_counter)
                    add_alias(loc, f"Kabupaten {bare}", created_counter)
                    add_alias(loc, f"Kab. {bare}", created_counter)
                    add_alias(loc, f"Kab {bare}", created_counter)

        self.stdout.write(self.style.SUCCESS(
            f"Alias generation done. Created={created_counter[0]}"
        ))