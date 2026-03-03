import csv
import re
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from intel.models import Location, LocationAlias


def alias_norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


LEVEL_MAP = {
    "provinsi": "province",
    "province": "province",
    "kabupaten": "kabupaten",
    "kab.": "kabupaten",
    "kab": "kabupaten",
    "kota": "kota",
    "kecamatan": "kecamatan",
    "desa": "desa",
    "country": "country",
}


class Command(BaseCommand):
    help = "Import gazetteer_id.csv into Location + LocationAlias. Supports columns: name, level, province, aliases"

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            required=True,
            help="Path to gazetteer csv, e.g. ..\\crawler\\gazetteer_id.csv"
        )
        parser.add_argument(
            "--country",
            default="ID",
            help="Country code, default ID"
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report but do not write to DB"
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        path = opts["csv"]
        country = opts["country"]
        dry = opts["dry_run"]

        try:
            f = open(path, "r", encoding="utf-8")
        except Exception as e:
            raise CommandError(f"Cannot open file: {path} ({e})")

        created_loc = 0
        updated_loc = 0
        created_alias = 0
        skipped_alias = 0

        # cache province -> Location
        prov_cache = {}

        with f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise CommandError("CSV has no header.")

            for row in reader:
                name = (row.get("name") or "").strip()
                level_raw = (row.get("level") or "").strip().lower()
                province_name = (row.get("province") or "").strip()
                aliases_raw = (row.get("aliases") or "").strip()

                if not name:
                    continue

                level = LEVEL_MAP.get(level_raw, level_raw if level_raw else "unknown")

                parent = None
                if level in ["kota", "kabupaten", "kecamatan", "desa"] and province_name:
                    # ensure province exists
                    prov_key = alias_norm(province_name)
                    if prov_key in prov_cache:
                        parent = prov_cache[prov_key]
                    else:
                        if dry:
                            parent = None
                        else:
                            parent, _ = Location.objects.get_or_create(
                                name=province_name,
                                level="province",
                                country_code=country,
                                defaults={"geocode_quality": "unknown"}
                            )
                            prov_cache[prov_key] = parent

                if dry:
                    created_loc += 1
                    # count aliases
                    alias_list = [a.strip() for a in aliases_raw.split("|") if a.strip()]
                    created_alias += len(alias_list) + 1  # include name itself
                    continue

                loc_obj, created = Location.objects.get_or_create(
                    name=name,
                    level=level,
                    country_code=country,
                    defaults={
                        "parent": parent,
                        "geocode_quality": "unknown",
                        "is_active": True,
                    }
                )
                if created:
                    created_loc += 1
                else:
                    # update parent if missing
                    changed = False
                    if parent and loc_obj.parent_id is None:
                        loc_obj.parent = parent
                        changed = True
                    if loc_obj.level != level and level != "unknown":
                        loc_obj.level = level
                        changed = True
                    if changed:
                        loc_obj.save(update_fields=["parent", "level", "updated_at"])
                        updated_loc += 1

                # ensure alias for official name
                primary_alias = alias_norm(name)
                if primary_alias:
                    if not LocationAlias.objects.filter(location=loc_obj, alias_norm=primary_alias).exists():
                        LocationAlias.objects.create(
                            location=loc_obj,
                            alias_text=name,
                            alias_norm=primary_alias
                        )
                        created_alias += 1
                    else:
                        skipped_alias += 1

                # aliases
                alias_list = [a.strip() for a in aliases_raw.split("|") if a.strip()]
                for a in alias_list:
                    a_norm = alias_norm(a)
                    if not a_norm:
                        continue
                    if LocationAlias.objects.filter(location=loc_obj, alias_norm=a_norm).exists():
                        skipped_alias += 1
                        continue
                    LocationAlias.objects.create(
                        location=loc_obj,
                        alias_text=a,
                        alias_norm=a_norm
                    )
                    created_alias += 1

        self.stdout.write(self.style.SUCCESS(
            f"Gazetteer import done. dry_run={dry}\n"
            f"  created_locations: {created_loc}\n"
            f"  updated_locations: {updated_loc}\n"
            f"  created_aliases: {created_alias}\n"
            f"  skipped_aliases: {skipped_alias}\n"
        ))