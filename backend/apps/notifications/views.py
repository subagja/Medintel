from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def notification_status(request: HttpRequest) -> HttpResponse:
    """Endpoint JSON ringan untuk polling badge lonceng di topbar."""
    unread_count = Notification.objects.filter(
        recipient=request.user,
        is_read=False,
    ).count()

    recent = Notification.objects.filter(
        recipient=request.user,
    )[:8]

    return JsonResponse(
        {
            "unread_count": unread_count,
            "items": [
                {
                    "id": str(item.id),
                    "title": item.title,
                    "body": item.body,
                    "link_url": item.link_url,
                    "is_read": item.is_read,
                    "created_at": timezone.localtime(
                        item.created_at
                    ).strftime("%d %b %H:%M"),
                }
                for item in recent
            ],
        }
    )


@login_required
def notification_list(request: HttpRequest) -> HttpResponse:
    notifications = Notification.objects.filter(
        recipient=request.user,
    )

    paginator = Paginator(notifications, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_title": "Notifikasi",
        "active_menu": "notifications",
        "page_obj": page_obj,
    }

    return render(
        request,
        "notifications/notification_list.html",
        context,
    )


@login_required
@require_POST
def notification_mark_read(
    request: HttpRequest,
    notification_id,
) -> HttpResponse:
    notification = get_object_or_404(
        Notification,
        id=notification_id,
        recipient=request.user,
    )

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save(update_fields=["is_read", "read_at"])

    next_url = request.POST.get("next") or notification.link_url

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True})

    return redirect(next_url or "dashboard:overview")


@login_required
@require_POST
def notification_mark_all_read(request: HttpRequest) -> HttpResponse:
    Notification.objects.filter(
        recipient=request.user,
        is_read=False,
    ).update(
        is_read=True,
        read_at=timezone.now(),
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True})

    return redirect("notifications:list")
