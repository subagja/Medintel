from django.urls import path
from .api_views import SignalsListAPI, PointsListAPI, StatsAPI
from .api_errors import ErrorsAPI, GazetteerMissingAPI

urlpatterns = [
    path("signals/", SignalsListAPI.as_view(), name="api-signals"),
    path("points/", PointsListAPI.as_view(), name="api-points"),
    path("stats/", StatsAPI.as_view(), name="api-stats"),
    path("errors/", ErrorsAPI.as_view(), name="api-errors"),
    path("gazetteer/missing/", GazetteerMissingAPI.as_view(), name="api-gazetteer-missing"),
]