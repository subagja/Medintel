from django.urls import path
from . import views

app_name = "intel"

urlpatterns = [
    path("", views.dashboard_overview, name="dashboard"),
    path("dashboard/", views.dashboard_overview, name="dashboard"),

    path("raw-signals/", views.raw_signals_list, name="raw_signals"),
    path("verified-signals/", views.verified_signals_list, name="verified_signals"),

    path("geocode-errors/", views.geocode_error_center, name="geocode_error_center"),
    path("geocode-errors/<int:pk>/edit/", views.geocode_manual_update, name="geocode_manual_update"),
    path("geocode-errors/<int:pk>/mark-manual/", views.geocode_mark_manual_ok, name="geocode_mark_manual_ok"),

    path("gazetteer/", views.gazetteer_manager, name="gazetteer_manager"),
    path("gazetteer/create/", views.gazetteer_location_create, name="gazetteer_location_create"),
    path("gazetteer/<int:pk>/edit/", views.gazetteer_location_edit, name="gazetteer_location_edit"),
    path("gazetteer/<int:pk>/toggle-false-positive/", views.gazetteer_toggle_false_positive, name="gazetteer_toggle_false_positive"),
    path("gazetteer/<int:pk>/toggle-active/", views.gazetteer_toggle_active, name="gazetteer_toggle_active"),

    path("gazetteer-aliases/", views.gazetteer_alias_manager, name="gazetteer_alias_manager"),
    path("gazetteer-aliases/create/", views.gazetteer_alias_create, name="gazetteer_alias_create"),

    path("signals/<int:pk>/validate/", views.signal_mark_validated, name="signal_validate"),
    path("signals/<int:pk>/noise/", views.signal_mark_noise, name="signal_noise"),
    path("signals/<int:pk>/approve/", views.signal_approve_mapping, name="signal_approve"),
    path("signals/<int:pk>/quick-score/", views.signal_quick_score, name="signal_quick_score"),

    path("scoring-rules/", views.scoring_rules_manager, name="scoring_rules_manager"),
    path("scoring-rules/create/", views.scoring_rule_create, name="scoring_rule_create"),
    path("scoring-rules/<int:pk>/edit/", views.scoring_rule_edit, name="scoring_rule_edit"),
    path("scoring-rules/<int:pk>/toggle-active/", views.scoring_rule_toggle_active, name="scoring_rule_toggle_active"),

    path("system-settings/create/", views.system_setting_create, name="system_setting_create"),
    path("system-settings/<int:pk>/edit/", views.system_setting_edit, name="system_setting_edit"),

    path("reports/", views.reports_generator, name="reports_generator"),
    path("reports/export-csv/", views.export_signals_csv, name="export_signals_csv"),

    path("alerts/", views.alert_center, name="alert_center"),
    path("alerts/generate/", views.generate_alerts, name="generate_alerts"),
    path("alerts/<int:pk>/edit/", views.alert_update_status, name="alert_update_status"),

    path("user-roles/", views.user_role_management, name="user_role_management"),
    path("user-roles/update/", views.user_role_update, name="user_role_update"),

    path("map/", views.map_intelligence, name="map_intelligence"),
    path("api/map-points/", views.map_points_api, name="map_points_api"),

    path("map-thematic/", views.map_thematic, name="map_thematic"),
    path("api/map-thematic-data/", views.map_thematic_data_api, name="map_thematic_data_api"),
]