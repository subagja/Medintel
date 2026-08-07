"""Pengiriman notifikasi (in-app + email) untuk kejadian penting sistem.

Dipanggil dari titik-titik pemicu di service layer (bukan dari view),
supaya notifikasi tetap terkirim baik aksi dilakukan lewat halaman web,
management command, maupun API di masa depan.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import Q

from apps.accounts.permissions import Roles
from .models import Notification


logger = logging.getLogger(__name__)

# Role yang menerima notifikasi kejadian operasional (sinyal, early
# warning). Viewer sengaja tidak diikutkan -- perannya hanya baca,
# notifikasi kejadian yang perlu ditindaklanjuti tidak relevan buat
# mereka dan hanya akan jadi noise.
DEFAULT_RECIPIENT_ROLES = (
    Roles.ADMIN,
    Roles.ANALYST,
    Roles.REVIEWER,
)


def _recipients_for_roles(roles):
    User = get_user_model()

    # Superuser selalu ikut menerima (konsisten dengan has_role() yang
    # menganggap superuser punya semua role), plus siapapun yang jadi
    # anggota salah satu Group yang diminta.
    return User.objects.filter(
        is_active=True,
    ).filter(
        Q(is_superuser=True) | Q(groups__name__in=roles)
    ).distinct()


def notify_users(
    *,
    notification_type: str,
    title: str,
    body: str = "",
    link_url: str = "",
    roles=DEFAULT_RECIPIENT_ROLES,
    exclude_user=None,
) -> int:
    """Buat notifikasi in-app untuk semua user aktif di `roles`, dan
    kirim email ringkasan yang sama. Mengembalikan jumlah penerima.

    `exclude_user` dipakai supaya orang yang MELAKUKAN aksi (mis.
    reviewer yang mengeskalasi sinyal) tidak menerima notifikasi atas
    aksinya sendiri.
    """
    recipients = list(_recipients_for_roles(roles))

    if exclude_user is not None:
        recipients = [
            user
            for user in recipients
            if user.pk != getattr(exclude_user, "pk", None)
        ]

    if not recipients:
        return 0

    Notification.objects.bulk_create(
        [
            Notification(
                recipient=user,
                notification_type=notification_type,
                title=title,
                body=body,
                link_url=link_url,
            )
            for user in recipients
        ]
    )

    _send_email_notifications(
        recipients=recipients,
        title=title,
        body=body,
        link_url=link_url,
    )

    return len(recipients)


def _send_email_notifications(*, recipients, title, body, link_url):
    email_addresses = [
        user.email
        for user in recipients
        if user.email
    ]

    if not email_addresses:
        return

    site_url = getattr(
        settings,
        "SITE_BASE_URL",
        "",
    ).rstrip("/")

    full_link = (
        f"{site_url}{link_url}"
        if site_url and link_url
        else link_url
    )

    message_lines = [body, ""]
    if full_link:
        message_lines.append(f"Buka: {full_link}")

    try:
        send_mail(
            subject=f"[MedIntel OSINT] {title}",
            message="\n".join(message_lines),
            from_email=getattr(
                settings,
                "DEFAULT_FROM_EMAIL",
                "medintel@localhost",
            ),
            recipient_list=email_addresses,
            fail_silently=True,
        )
    except Exception:
        # Kegagalan kirim email tidak boleh menggagalkan alur bisnis
        # utama (mis. eskalasi sinyal tetap harus tersimpan walau
        # email gagal terkirim karena SMTP belum dikonfigurasi).
        logger.exception(
            "Gagal mengirim email notifikasi: %s",
            title,
        )
