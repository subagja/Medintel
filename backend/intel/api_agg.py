from django.db.models import Count, Avg, Max
from rest_framework.views import APIView
from rest_framework.response import Response
from collections import defaultdict
from .models import SignalLocation
from .models import Signal

from collections import Counter
from datetime import date

class AggProvinceAPI(APIView):
    def get(self, request):
        min_score = request.query_params.get("min_score", 0)
        try:
            min_score = int(min_score)
        except Exception:
            min_score = 0

        qs = SignalLocation.objects.select_related("signal", "location").filter(
            is_primary=True,
            geocode_status="ok",
            signal__threat_score__gte=min_score,
            location__isnull=False
        )

        # group by province (ambil via parent traversal di python sederhana)
        # versi cepat: ambil semua dan hitung manual
        rows = []
        for sl in qs.only("id", "location__id", "location__name", "location__level", "location__parent_id", "signal__threat_score"):
            rows.append(sl)

        def get_prov(loc):
            cur = loc
            for _ in range(6):
                if cur is None:
                    return None
                if cur.level == "province":
                    return cur.name
                cur = cur.parent
            return None

        agg = {}
        for sl in rows:
            prov = get_prov(sl.location)
            if not prov:
                continue
            if prov not in agg:
                agg[prov] = {"province": prov, "count": 0, "sum_score": 0, "max_score": 0}
            agg[prov]["count"] += 1
            agg[prov]["sum_score"] += sl.signal.threat_score or 0
            agg[prov]["max_score"] = max(agg[prov]["max_score"], sl.signal.threat_score or 0)

        out = []
        for prov, v in agg.items():
            avg_score = round(v["sum_score"] / v["count"], 2) if v["count"] else 0
            out.append({
                "province": prov,
                "count": v["count"],
                "avg_score": avg_score,
                "max_score": v["max_score"],
            })

        out.sort(key=lambda x: x["count"], reverse=True)
        return Response(out)

class AggProvincePointsAPI(APIView):
    """
    GET /api/agg/province-points/?min_score=35
    Returns aggregated markers per province using Location hierarchy.
    """
    def get(self, request):
        try:
            min_score = int(request.query_params.get("min_score", 0))
        except Exception:
            min_score = 0

        qs = (
            SignalLocation.objects
            .select_related("signal", "location", "location__parent", "location__parent__parent")
            .filter(
                is_primary=True,
                geocode_status="ok",
                signal__threat_score__gte=min_score,
                location__isnull=False
            )
        )

        def walk_to_prov(loc, max_hops=8):
            cur = loc
            for _ in range(max_hops):
                if not cur:
                    return None
                if cur.level == "province":
                    return cur
                cur = cur.parent
            return None

        agg = defaultdict(lambda: {"count": 0, "sum_score": 0, "max_score": 0, "lat": None, "lon": None})

        for sl in qs:
            prov = walk_to_prov(sl.location)
            if not prov:
                continue

            key = prov.name
            agg[key]["count"] += 1
            score = sl.signal.threat_score or 0
            agg[key]["sum_score"] += score
            agg[key]["max_score"] = max(agg[key]["max_score"], score)

            # ambil centroid provinsi dari Location (kalau sudah diisi)
            if agg[key]["lat"] is None and prov.lat is not None and prov.lon is not None:
                agg[key]["lat"] = float(prov.lat)
                agg[key]["lon"] = float(prov.lon)

        out = []
        for prov_name, v in agg.items():
            avg = round(v["sum_score"] / v["count"], 2) if v["count"] else 0
            out.append({
                "province": prov_name,
                "count": v["count"],
                "avg_score": avg,
                "max_score": v["max_score"],
                "lat": v["lat"],
                "lon": v["lon"],
            })

        out.sort(key=lambda x: x["count"], reverse=True)
        return Response(out)

class TrendAPI(APIView):

    def get(self, request):

        min_score = request.query_params.get("min_score", 0)

        try:
            min_score = int(min_score)
        except:
            min_score = 0

        qs = Signal.objects.filter(
            threat_score__gte=min_score
        )

        counter = Counter()

        for s in qs.only("published_at"):
            if s.published_at:
                d = s.published_at.date()
                counter[d] += 1

        out = []

        for d in sorted(counter):
            out.append({
                "date": d,
                "count": counter[d]
            })

        return Response(out)

class DiseaseDistAPI(APIView):

    def get(self, request):

        qs = Signal.objects.all()

        counter = Counter()

        for s in qs.only("disease_tag"):
            counter[s.disease_tag] += 1

        out = []

        for k,v in counter.most_common():
            out.append({
                "disease":k,
                "count":v
            })

        return Response(out)