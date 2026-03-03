from django.db.models import Count, Q
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import SignalLocation, LocationAlias


def _as_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default


class ErrorsAPI(APIView):
    """
    GET /api/errors/?min_score=35&tag=DBD&only_primary=1&limit=200&status=not_found,empty,net_err
    Returns:
      - summary: counts by geocode_status
      - rows: list of recent SignalLocation errors (with signal fields)
    """

    def get(self, request):
        min_score = request.query_params.get("min_score")
        tag = request.query_params.get("tag")
        only_primary = request.query_params.get("only_primary", "1")
        limit = _as_int(request.query_params.get("limit", 200), 200)
        status_param = (request.query_params.get("status") or "").strip()

        qs = SignalLocation.objects.select_related("signal", "signal__source")

        # Usually we focus on errors; default excludes OK
        if status_param:
            statuses = [s.strip() for s in status_param.split(",") if s.strip()]
            qs = qs.filter(geocode_status__in=statuses)
        else:
            qs = qs.exclude(geocode_status="ok")

        if only_primary in ["1", "true", "True", "yes"]:
            qs = qs.filter(is_primary=True)

        if min_score:
            try:
                qs = qs.filter(signal__threat_score__gte=int(min_score))
            except Exception:
                pass

        if tag:
            qs = qs.filter(signal__disease_tag=tag)

        # Summary counts
        summary = (
            qs.values("geocode_status")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        # Rows
        rows = []
        for sl in qs.order_by("-signal__published_at", "-signal__crawled_at")[:limit]:
            rows.append({
                "id": sl.id,
                "signal_id": sl.signal_id,
                "published_at": sl.signal.published_at,
                "crawled_at": sl.signal.crawled_at,
                "disease_tag": sl.signal.disease_tag,
                "threat_score": sl.signal.threat_score,
                "signal_status": sl.signal.status,
                "title": sl.signal.title,
                "source_name": sl.signal.source.name if sl.signal.source_id else None,
                "url": sl.signal.url,
                "final_url": sl.signal.final_url,
                "raw_location_text": sl.raw_location_text,
                "confidence": sl.confidence,
                "method": sl.method,
                "geocode_status": sl.geocode_status,
                "lat": sl.lat,
                "lon": sl.lon,
                "is_primary": sl.is_primary,
            })

        return Response({
            "summary": list(summary),
            "rows": rows
        })


class GazetteerMissingAPI(APIView):
    """
    GET /api/gazetteer/missing/?min_score=35&tag=DBD&only_primary=1&limit=100&status=not_found,empty
    Returns top raw_location_text that are:
      - not OK
      - not empty
      - and NOT present in LocationAlias.alias_norm (normalized)
    """

    def get(self, request):
        min_score = request.query_params.get("min_score")
        tag = request.query_params.get("tag")
        only_primary = request.query_params.get("only_primary", "1")
        limit = _as_int(request.query_params.get("limit", 100), 100)
        status_param = (request.query_params.get("status") or "").strip()

        qs = SignalLocation.objects.select_related("signal")

        # focus errors; default excludes OK
        if status_param:
            statuses = [s.strip() for s in status_param.split(",") if s.strip()]
            qs = qs.filter(geocode_status__in=statuses)
        else:
            qs = qs.exclude(geocode_status="ok")

        if only_primary in ["1", "true", "True", "yes"]:
            qs = qs.filter(is_primary=True)

        if min_score:
            try:
                qs = qs.filter(signal__threat_score__gte=int(min_score))
            except Exception:
                pass

        if tag:
            qs = qs.filter(signal__disease_tag=tag)

        # exclude null/blank raw_location_text
        qs = qs.exclude(Q(raw_location_text__isnull=True) | Q(raw_location_text__exact=""))

        # Build alias set (normalized) in-memory for fast membership.
        # If your aliases become huge, we can optimize using DB-side normalization later.
        alias_set = set(
            LocationAlias.objects.values_list("alias_norm", flat=True)
        )

        # Aggregate counts by raw_location_text (DB)
        agg = (
            qs.values("raw_location_text")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        # Filter out those already known in gazetteer aliases (using simple normalization rules)
        # NOTE: We compare with a very light normalization here to avoid importing regex-heavy pipeline.
        def norm_light(s: str) -> str:
            s = (s or "").lower().strip()
            # remove punctuation -> space
            import re
            s = re.sub(r"[^a-z0-9\s\-]", " ", s)
            s = re.sub(r"\s+", " ", s).strip()
            return s

        out = []
        for item in agg:
            raw_loc = item["raw_location_text"]
            raw_norm = norm_light(raw_loc)

            # If already exists in aliases -> skip
            if raw_norm in alias_set:
                continue

            out.append({
                "raw_location_text": raw_loc,
                "raw_norm": raw_norm,
                "count": item["count"],
            })

            if len(out) >= limit:
                break

        return Response({
            "limit": limit,
            "results": out
        })