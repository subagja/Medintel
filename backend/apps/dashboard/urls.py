from django.urls import path

from . import views


app_name = "dashboard"

urlpatterns = [
    path(
        "",
        views.dashboard_overview,
        name="overview",
    ),
    path(
        "validasi-artikel/",
        views.article_validation,
        name="article-validation",
    ),
]