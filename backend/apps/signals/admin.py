from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import Signal
from .services import (
    close_signal,
    escalate_signal,
    reject_signal,
    start_signal_review,
    validate_signal,
)


@admin.register(Signal)
class SignalAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "title",
        "primary_disease",
        "primary_location",
        "status",
        "priority_level",
        "confidence_level",
        "event_classification",
        "system_score",
        "updated_at",
        "assigned_to",
        "validated_by",
        "validated_at",
        "event_start_date",
        "event_end_date",
        "last_updated_at",
    )

    list_filter = (
        "status",
        "priority_level",
        "confidence_level",
        "event_classification",
        "primary_disease",
        "primary_location",
    )

    fieldsets = (
        (
            "Identitas Sinyal",
            {
                "fields": (
                    "id",
                    "code",
                    "title",
                    "summary",
                )
            },
        ),
        (
            "Kejadian",
            {
                "fields": (
                    "primary_disease",
                    "primary_location",
                    "event_start_date",
                    "event_end_date",
                    "event_classification",
                    "classification_basis",
                )
            },
        ),
        (
            "Penilaian Awal Sistem",
            {
                "fields": (
                    "system_score",
                    "priority_level",
                    "confidence_level",
                    "created_by_system",
                )
            },
        ),
        (
            "Review Analis",
            {
                "fields": (
                    "status",
                    "assigned_to",
                    "validated_by",
                    "validated_at",
                    "validation_notes",
                    "analyst_judgement",
                    "implication",
                    "recommended_action",
                    "information_gaps",
                )
            },
        ),
        (
            "Waktu",
            {
                "fields": (
                    "first_detected_at",
                    "last_updated_at",
                    "closed_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    search_fields = (
        "code",
        "title",
        "summary",
        "analyst_judgement",
        "implication",
        "recommended_action",
        "primary_disease__name",
        "primary_location__name",
    )

    readonly_fields = (
        "id",
        "code",
        "system_score",      
        "first_detected_at",
        "last_updated_at",
        "validated_at",
        "closed_at",
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "primary_disease",
        "primary_location",
    )

    ordering = (
        "-updated_at",
    )

    actions = [
        "start_review_selected",
        "validate_selected",
        "reject_selected",
        "escalate_selected",
        "close_selected",
    ]

    @admin.action(
        description="Mulai review sinyal terpilih"
    )
    def start_review_selected(
        self,
        request,
        queryset,
    ):
        success = 0
        skipped = 0

        for signal in queryset:
            try:
                start_signal_review(
                    signal=signal,
                    reviewer=request.user,
                    notes=(
                        "Review dimulai melalui "
                        "Django Admin."
                    ),
                )
                success += 1

            except ValidationError:
                skipped += 1

        self.message_user(
            request,
            (
                f"{success} sinyal masuk tahap review. "
                f"{skipped} sinyal dilewati."
            ),
        )

    @admin.action(
        description="Validasi sinyal terpilih"
    )
    def validate_selected(
        self,
        request,
        queryset,
    ):
        success = 0
        skipped = 0

        for signal in queryset:
            try:
                validate_signal(
                    signal=signal,
                    reviewer=request.user,
                    judgement=(
                        signal.analyst_judgement
                        or (
                            "Sinyal sesuai dengan indikator "
                            "dan bukti pendukung."
                        )
                    ),
                    implication=signal.implication,
                    recommended_action=(
                        signal.recommended_action
                    ),
                    information_gaps=(
                        signal.information_gaps
                    ),
                    notes=(
                        "Validasi melalui Django Admin."
                    ),
                )
                success += 1

            except ValidationError:
                skipped += 1

        self.message_user(
            request,
            (
                f"{success} sinyal berhasil divalidasi. "
                f"{skipped} sinyal dilewati."
            ),
        )

    @admin.action(
        description="Tolak sinyal terpilih"
    )
    def reject_selected(
        self,
        request,
        queryset,
    ):
        success = 0
        skipped = 0

        for signal in queryset:
            try:
                reject_signal(
                    signal=signal,
                    reviewer=request.user,
                    notes=(
                        "Ditolak melalui Django Admin."
                    ),
                )
                success += 1

            except ValidationError:
                skipped += 1

        self.message_user(
            request,
            (
                f"{success} sinyal berhasil ditolak. "
                f"{skipped} sinyal dilewati."
            ),
        )

    @admin.action(
        description="Eskalasi sinyal terpilih"
    )
    def escalate_selected(
        self,
        request,
        queryset,
    ):
        success = 0
        skipped = 0

        for signal in queryset:
            try:
                escalate_signal(
                    signal=signal,
                    reviewer=request.user,
                    notes=(
                        "Dieskalasi melalui Django Admin."
                    ),
                    priority_level=(
                        Signal.PriorityLevel.HIGH
                    ),
                )
                success += 1

            except ValidationError:
                skipped += 1

        self.message_user(
            request,
            (
                f"{success} sinyal berhasil dieskalasi. "
                f"{skipped} sinyal dilewati."
            ),
        )

    @admin.action(
        description="Tutup sinyal terpilih"
    )
    def close_selected(
        self,
        request,
        queryset,
    ):
        success = 0
        skipped = 0

        for signal in queryset:
            try:
                close_signal(
                    signal=signal,
                    reviewer=request.user,
                    notes=(
                        "Sinyal ditutup melalui "
                        "Django Admin."
                    ),
                )
                success += 1

            except ValidationError:
                skipped += 1

        self.message_user(
            request,
            (
                f"{success} sinyal berhasil ditutup. "
                f"{skipped} sinyal dilewati."
            ),
        )
