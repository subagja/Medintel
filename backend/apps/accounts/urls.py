from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path(
        "peran/",
        views.user_role_list,
        name="user-role-list",
    ),
    path(
        "peran/tambah/",
        views.user_create,
        name="user-create",
    ),
    path(
        "peran/<int:user_id>/ubah/",
        views.user_role_update,
        name="user-role-update",
    ),
]
