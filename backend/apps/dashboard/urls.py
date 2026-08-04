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
    path(
        "crawler-artikel/",
        views.crawler_list,
        name="crawler-list",
    ),
    path(
        "crawler-artikel/jalankan/",
        views.crawler_run,
        name="crawler-run",
    ),
    path(
        "crawler-artikel/job/<uuid:job_id>/",
        views.crawler_job_detail,
        name="crawler-job-detail",
    ),
    path(
        "sumber-osint/",
        views.source_list,
        name="source-list",
    ),
    path(
        "sumber-osint/<int:source_id>/",
        views.source_detail,
        name="source-detail",
    ),
    path(
        "sumber-osint/tambah/",
        views.source_create,
        name="source-create",
    ),
    path(
        "sumber-osint/<int:source_id>/edit/",
        views.source_update,
        name="source-update",
    ),
    path(
        "sumber-osint/<int:source_id>/url-awal/tambah/",
        views.source_seed_create,
        name="source-seed-create",
    ),
    path(
        (
            "sumber-osint/<int:source_id>/url-awal/"
            "<uuid:seed_id>/edit/"
        ),
        views.source_seed_update,
        name="source-seed-update",
    ),
    path(
        "sumber-osint/<int:source_id>/pola-url/tambah/",
        views.source_pattern_create,
        name="source-pattern-create",
    ),
    path(
        (
            "sumber-osint/<int:source_id>/pola-url/"
            "<uuid:pattern_id>/edit/"
        ),
        views.source_pattern_update,
        name="source-pattern-update",
    ),
]
