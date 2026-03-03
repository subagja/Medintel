import re
from django.core.management.base import BaseCommand
from django.db import transaction

from intel.models import LocationAlias, SignalLocation


def alias_norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def candidates_from_raw(raw: str) -> list[str]:
    """
    Generate normalized candidates from raw_location_text:
    "Kota Bogor, Jawa Barat" -> ["kota bogor jawa barat", "bogor jawa barat", "bogor"]
    """
    raw = (raw or "").strip()
    if not raw:
        return []

    # split commas, keep first pieces too
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    joined = " ".join(parts)

    # remove admin prefixes
    def strip_admin(x: str) -> str:
        return re.sub(r"^(kota|kabupaten|kab\.|kab|provinsi|prov\.)\s+", "", x.strip(), flags=re.I).strip()

    cands = []
    cands.append(alias_norm(joined))

    if parts:
        first = strip_admin(parts[0])
        rest = " ".join([first] + parts[1:]) if first else " ".join(parts[1:])
        if rest.strip():
            cands.append(alias_norm(rest))

    if parts:
        main = strip_admin(parts[0])
        if main:
            cands.append(alias_norm(main))

    # unique preserve order
    out = []
    seen = set()
    for c in cands:
        if c and c not in seen:
            out.append(c)
            seen.add(c)
    return out


class Command(BaseCommand):
    help = "Auto-link SignalLocation.location using LocationAlias matching."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Limit rows processed (0 = all)")
        parser.add_argument("--dry-run", action="store_true", help="Do not write changes")
        parser.add_argument(
            "--only-unlinked",
            action="store_true",
            help="Only process SignalLocation where location is NULL"
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        limit = opts["limit"]
        dry = opts["dry_run"]
        only_unlinked = opts["only_unlinked"]

        qs = SignalLocation.objects.all().select_related("location", "signal")
        if only_unlinked:
            qs = qs.filter(location__isnull=True)

        if limit and limit > 0:
            qs = qs[:limit]

        matched = 0
        not_matched = 0
        skipped_empty = 0

        # Build alias map in-memory for speed: alias_norm -> location_id
        # If same alias points to multiple, we take the first (can be refined later)
        alias_map = {}
        for a in LocationAlias.objects.select_related("location").all():
            if a.alias_norm and a.alias_norm not in alias_map:
                alias_map[a.alias_norm] = a.location_id

        for sl in qs:
            raw = (sl.raw_location_text or "").strip()
            if not raw:
                skipped_empty += 1
                continue

            cands = candidates_from_raw(raw)
            loc_id = None
            for c in cands:
                if c in alias_map:
                    loc_id = alias_map[c]
                    break

            if not loc_id:
                not_matched += 1
                continue

            matched += 1
            if dry:
                continue

            sl.location_id = loc_id
            sl.save(update_fields=["location"])

        self.stdout.write(self.style.SUCCESS(
            f"Linking done. dry_run={dry}\n"
            f"  matched: {matched}\n"
            f"  not_matched: {not_matched}\n"
            f"  skipped_empty: {skipped_empty}\n"
        ))