from django.urls import path

from . import views


app_name = "notifications"

urlpatterns = [
    path(
        "",
        views.notification_list,
        name="list",
    ),
    path(
        "status/",
        views.notification_status,
        name="status",
    ),
    path(
        "<uuid:notification_id>/baca/",
        views.notification_mark_read,
        name="mark-read",
    ),
    path(
        "tandai-semua-dibaca/",
        views.notification_mark_all_read,
        name="mark-all-read",
    ),
]
