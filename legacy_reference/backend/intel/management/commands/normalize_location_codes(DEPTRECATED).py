import re
import unicodedata

from django.core.management.base import BaseCommand
from intel.models import Location


def normalize_region_code(value: str) -> str:
    if not value:
        return ""
    value = str(value).strip().lower()
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.replace("/", " ")
    value = re.sub(r"[^a-z0-9\s_-]", "", value)
    value = re.sub(r"[\s\-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


class Command(BaseCommand):
    help = "Normalize province_code and city_regency_code in Location"

    def handle(self, *args, **options):
        updated = 0

        for loc in Location.objects.select_related("parent").all():
            changed = False

            if not loc.normalized_name or loc.normalized_name != normalize_region_code(loc.name):
                loc.normalized_name = normalize_region_code(loc.name)
                changed = True

            if loc.level == "province":
                new_code = normalize_region_code(loc.display_name or loc.name)
                if loc.province_code != new_code:
                    loc.province_code = new_code
                    changed = True

            if loc.level in ["city", "regency"]:
                new_city_code = normalize_region_code(loc.display_name or loc.name)
                if loc.city_regency_code != new_city_code:
                    loc.city_regency_code = new_city_code
                    changed = True

                if loc.parent and loc.parent.level == "province":
                    new_prov_code = loc.parent.province_code or normalize_region_code(loc.parent.display_name or loc.parent.name)
                    if loc.province_code != new_prov_code:
                        loc.province_code = new_prov_code
                        changed = True

            if changed:
                loc.save()
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Normalization selesai. Location terupdate: {updated}"))