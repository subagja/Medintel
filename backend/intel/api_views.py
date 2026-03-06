# from collections import Counter

# from rest_framework.views import APIView
# from rest_framework.response import Response
# from rest_framework.generics import ListAPIView
# from rest_framework.pagination import PageNumberPagination

# from .models import Signal, SignalLocation
# from .serializers import SignalSerializer, PointSerializer


# class StandardResultsSetPagination(PageNumberPagination):
#     page_size = 50
#     page_size_query_param = "page_size"
#     max_page_size = 200


# class SignalsListAPI(ListAPIView):
#     """
#     /api/signals/?tag=DBD&min_score=35&status=raw&search=bogor
#     """
#     serializer_class = SignalSerializer
#     pagination_class = StandardResultsSetPagination

#     def get_queryset(self):
#         qs = Signal.objects.all().select_related("source").order_by("-published_at", "-crawled_at")

#         tag = self.request.query_params.get("tag")
#         status = self.request.query_params.get("status")
#         min_score = self.request.query_params.get("min_score")
#         search = self.request.query_params.get("search")

#         if tag:
#             qs = qs.filter(disease_tag=tag)

#         if status:
#             qs = qs.filter(status=status)

#         if min_score:
#             try:
#                 qs = qs.filter(threat_score__gte=int(min_score))
#             except Exception:
#                 pass

#         if search:
#             qs = qs.filter(title__icontains=search)

#         return qs


# class PointsListAPI(ListAPIView):
#     """
#     /api/points/?tag=DBD&min_score=35
#     """
#     serializer_class = PointSerializer
#     pagination_class = None  # biasanya point ingin full list untuk map

#     def get_queryset(self):
#         qs = (
#             SignalLocation.objects.filter(is_primary=True, geocode_status="ok")
#             .select_related("signal", "signal__source")
#             .order_by("-signal__published_at", "-signal__crawled_at")
#         )

#         tag = self.request.query_params.get("tag")
#         min_score = self.request.query_params.get("min_score")

#         if tag:
#             qs = qs.filter(signal__disease_tag=tag)

#         if min_score:
#             try:
#                 qs = qs.filter(signal__threat_score__gte=int(min_score))
#             except Exception:
#                 pass

#         return qs


# class StatsAPI(APIView):
#     """
#     /api/stats/?min_score=35
#     Returns counts for dashboard: by_tag, by_status, by_geocode_status
#     """
#     def get(self, request):
#         min_score = request.query_params.get("min_score")

#         qs = Signal.objects.all()
#         if min_score:
#             try:
#                 qs = qs.filter(threat_score__gte=int(min_score))
#             except Exception:
#                 pass

#         by_tag = Counter()
#         by_status = Counter()
#         for s in qs.only("disease_tag", "status").iterator(chunk_size=2000):
#             by_tag[s.disease_tag] += 1
#             by_status[s.status] += 1

#         # geocode stats from SignalLocation primary
#         sl_qs = SignalLocation.objects.filter(is_primary=True)
#         by_geo = Counter()
#         for sl in sl_qs.only("geocode_status").iterator(chunk_size=2000):
#             by_geo[sl.geocode_status] += 1

#         return Response({
#             "by_tag": dict(by_tag),
#             "by_status": dict(by_status),
#             "by_geocode_status": dict(by_geo),
#         })

from collections import Counter

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination

from .models import Signal, SignalLocation
from .serializers import SignalSerializer, PointSerializer


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


class SignalsListAPI(ListAPIView):
    """
    /api/signals/?tag=DBD&min_score=35&status=raw&search=bogor
    """
    serializer_class = SignalSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = Signal.objects.all().select_related("source").order_by("-published_at", "-crawled_at", "-created_at")

        tag = self.request.query_params.get("tag")
        status = self.request.query_params.get("status")
        min_score = self.request.query_params.get("min_score")
        search = self.request.query_params.get("search")

        if tag:
            qs = qs.filter(disease_tag=tag)

        if status:
            qs = qs.filter(status=status)

        if min_score:
            try:
                qs = qs.filter(threat_score__gte=int(min_score))
            except Exception:
                pass

        if search:
            qs = qs.filter(title__icontains=search)

        return qs


class PointsListAPI(ListAPIView):
    """
    /api/points/?tag=DBD&min_score=35
    """
    serializer_class = PointSerializer
    pagination_class = None

    def get_queryset(self):
        qs = (
            SignalLocation.objects.filter(is_primary=True, geocode_status="ok")
            .select_related(
                "signal",
                "signal__source",
                "location",
                "location__parent",
                "location__parent__parent",
            )
            .order_by("-signal__published_at", "-signal__crawled_at", "-signal__created_at")
        )

        tag = self.request.query_params.get("tag")
        min_score = self.request.query_params.get("min_score")

        if tag:
            qs = qs.filter(signal__disease_tag=tag)

        if min_score:
            try:
                qs = qs.filter(signal__threat_score__gte=int(min_score))
            except Exception:
                pass

        return qs


class StatsAPI(APIView):
    """
    /api/stats/?min_score=35
    Returns counts for dashboard:
    - by_tag
    - by_status
    - by_geocode_status
    - by_event_type
    """
    def get(self, request):
        min_score = request.query_params.get("min_score")

        qs = Signal.objects.all()
        if min_score:
            try:
                qs = qs.filter(threat_score__gte=int(min_score))
            except Exception:
                pass

        by_tag = Counter()
        by_status = Counter()
        by_event_type = Counter()

        for s in qs.only("disease_tag", "status", "event_types").iterator(chunk_size=2000):
            by_tag[s.disease_tag] += 1
            by_status[s.status] += 1

            if s.event_types:
                for ev in str(s.event_types).split("|"):
                    ev = ev.strip()
                    if ev:
                        by_event_type[ev] += 1

        sl_qs = SignalLocation.objects.filter(is_primary=True)
        by_geo = Counter()
        for sl in sl_qs.only("geocode_status").iterator(chunk_size=2000):
            by_geo[sl.geocode_status] += 1

        return Response({
            "by_tag": dict(by_tag),
            "by_status": dict(by_status),
            "by_geocode_status": dict(by_geo),
            "by_event_type": dict(by_event_type),
        })