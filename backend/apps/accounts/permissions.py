"""Konstanta role & helper enforcement untuk view MedIntel.

Role dikelola lewat Django Group bawaan (lihat migration
`0001_setup_roles.py` untuk daftar permission per role). Modul ini
menyediakan cara praktis untuk mengecek role dari dalam view/template.
"""
from __future__ import annotations

from functools import wraps
from typing import Callable

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse


class Roles:
    ADMIN = "Admin"
    ANALYST = "Analyst"
    REVIEWER = "Reviewer"
    VIEWER = "Viewer"

    ALL = (ADMIN, ANALYST, REVIEWER, VIEWER)
    # Role yang boleh melakukan aksi tulis/mutasi (bukan sekadar baca).
    CONTRIBUTORS = (ADMIN, ANALYST, REVIEWER)
    # Role yang boleh melakukan approval/eskalasi.
    APPROVERS = (ADMIN, REVIEWER)


def user_roles(user) -> set[str]:
    """Kumpulan nama Group (role) milik user, kosong kalau anonim."""
    if not user or not user.is_authenticated:
        return set()
    if user.is_superuser:
        return set(Roles.ALL)
    return set(user.groups.values_list("name", flat=True))


def has_role(user, *roles: str) -> bool:
    return bool(user_roles(user) & set(roles))


def require_role(*roles: str) -> Callable:
    """Decorator view: wajib login DAN termasuk salah satu `roles`.

    Superuser selalu lolos (perilaku standar Django). Urutan pemakaian:

        @require_role(Roles.ADMIN, Roles.ANALYST)
        def crawler_run(request):
            ...

    Kalau user login tapi role-nya tidak cocok -> 403 (PermissionDenied),
    yang oleh Django dirender lewat template `403.html` jika ada.
    Kalau user belum login -> diarahkan ke halaman login (perilaku
    `login_required` standar, menghormati setting LOGIN_URL).
    """

    def decorator(view_func: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @wraps(view_func)
        @login_required
        def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            if not has_role(request.user, *roles):
                raise PermissionDenied(
                    "Anda tidak memiliki peran yang diizinkan untuk mengakses halaman ini."
                )
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
