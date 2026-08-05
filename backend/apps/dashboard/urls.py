from django.urls import path

from . import views
from apps.assessments import views as assessment_views
from apps.assessments import early_warning_views
from apps.assessments import threat_map_views
from apps.signals import views as signal_views


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
        "sinyal-intelijen/",
        signal_views.signal_workspace,
        name="signal-workspace",
    ),
    path(
        "assessment-ancaman/",
        assessment_views.threat_assessment_workspace,
        name="threat-assessment",
    ),
    path(
        "peringatan-dini/",
        early_warning_views.early_warning_workspace,
        name="early-warning",
    ),
    path(
        "peta-ancaman/",
        threat_map_views.threat_map_workspace,
        name="threat-map",
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
