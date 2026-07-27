from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


ROLE_ADMIN = "Admin Sistem"
ROLE_ANALYST_SENIOR = "Analis Senior"
ROLE_ANALYST = "Analis"
ROLE_VIEWER = "Viewer"


def user_has_role(user, allowed_roles):
    if not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    return user.groups.filter(name__in=allowed_roles).exists()


def role_required(*allowed_roles):
    def decorator(view_func):
        @login_required
        def _wrapped_view(request, *args, **kwargs):
            if user_has_role(request.user, allowed_roles):
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("Anda tidak memiliki akses ke halaman ini.")
        return _wrapped_view
    return decorator