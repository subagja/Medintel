from django.urls import path
from .api_views import SignalsListAPI, PointsListAPI, StatsAPI
from .api_errors import ErrorsAPI, GazetteerMissingAPI
from .api_agg import AggProvinceAPI, AggProvincePointsAPI, TrendAPI, DiseaseDistAPI
from .api_alerts import OutbreakAlertAPI


urlpatterns = [
    path("signals/", SignalsListAPI.as_view(), name="api-signals"),
    path("points/", PointsListAPI.as_view(), name="api-points"),
    path("stats/", StatsAPI.as_view(), name="api-stats"),
    path("errors/", ErrorsAPI.as_view(), name="api-errors"),
    path("gazetteer/missing/", GazetteerMissingAPI.as_view(), name="api-gazetteer-missing"),
    path("agg/provinces/", AggProvinceAPI.as_view(), name="api-agg-provinces"),
    path("agg/province-points/", AggProvincePointsAPI.as_view(), name="agg-province-points"),
    path("agg/trend/", TrendAPI.as_view()),
    path("agg/disease/", DiseaseDistAPI.as_view()),
    path("alerts/outbreak/", OutbreakAlertAPI.as_view()),
]