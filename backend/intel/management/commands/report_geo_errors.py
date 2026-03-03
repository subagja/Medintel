from collections import Counter
from django.core.management.base import BaseCommand
from intel.models import SignalLocation


class Command(BaseCommand):
    help = "Report top raw_location_text causing geocode failures."

    def add_arguments(self, parser):
        parser.add_argument("--top", type=int, default=50)
        parser.add_argument("--status", default="", help="Filter by geocode_status (comma-separated)")
        parser.add_argument("--only-primary", action="store_true")

    def handle(self, *args, **opts):
        top_n = opts["top"]
        status = opts["status"].strip()
        only_primary = opts["only_primary"]

        qs = SignalLocation.objects.all()

        if status:
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            qs = qs.filter(geocode_status__in=statuses)

        if only_primary:
            qs = qs.filter(is_primary=True)

        counter = Counter()
        for sl in qs.iterator(chunk_size=2000):
            k = (sl.raw_location_text or "").strip()
            if not k:
                k = "(EMPTY_RAW_LOCATION_TEXT)"
            counter[k] += 1

        self.stdout.write("Top raw_location_text errors:\n")
        for loc, cnt in counter.most_common(top_n):
            self.stdout.write(f"{cnt:>5}  {loc}")