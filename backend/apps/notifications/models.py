import uuid

from django.conf import settings
from django.db import models


class Notification(models.Model):
    class NotificationType(models.TextChoices):
        SIGNAL_CREATED = (
            "signal_created",
            "Sinyal Baru Terbentuk",
        )
        SIGNAL_ESCALATED = (
            "signal_escalated",
            "Sinyal Dieskalasi",
        )
        EARLY_WARNING_ISSUED = (
            "early_warning_issued",
            "Early Warning Diterbitkan",
        )

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )

    notification_type = models.CharField(
        max_length=30,
        choices=NotificationType.choices,
    )

    title = models.CharField(
        max_length=200,
    )

    body = models.TextField(
        blank=True,
    )

    link_url = models.CharField(
        max_length=500,
        blank=True,
    )

    is_read = models.BooleanField(
        default=False,
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    read_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["recipient", "is_read"],
                name="notif_recipient_read_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_notification_type_display()} -> {self.recipient}"
