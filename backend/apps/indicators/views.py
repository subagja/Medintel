from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.permissions import Roles, has_role, require_role_by_method

from apps.signals.services.generation import generate_signal_from_indicator

from .forms import IndicatorReviewForm
from .models import Indicator
from .services.review import correct_indicator, reject_indicator, validate_indicator


@require_role_by_method(
    read_roles=Roles.ALL,
    write_roles=Roles.CONTRIBUTORS,
)
def indicator_review(request: HttpRequest) -> HttpResponse:
    indicators = (
        Indicator.objects
        .select_related("indicator_type", "disease", "location", "validated_by")
        .prefetch_related("evidences__article", "review_logs__reviewer", "signal_links__signal")
        .order_by("-created_at")
    )

    status_filter = request.GET.get("status", Indicator.Status.NEEDS_REVIEW)
    if status_filter != "all":
        indicators = indicators.filter(status=status_filter)

    selected_id = request.POST.get("indicator_id") or request.GET.get("indicator")
    selected_indicator = (
        get_object_or_404(indicators, id=selected_id)
        if selected_id
        else indicators.first()
    )

    review_form = None
    evidences = []
    review_logs = []
    linked_signals = []

    if selected_indicator:
        review_form = IndicatorReviewForm(request.POST or None, instance=selected_indicator)
        evidences = list(selected_indicator.evidences.select_related("article", "article_fact"))
        review_logs = list(selected_indicator.review_logs.select_related("reviewer"))
        linked_signals = list(selected_indicator.signal_links.select_related("signal"))

        if request.method == "POST":
            action = request.POST.get("action", "")
            notes = request.POST.get("review_notes", "").strip()

            if not request.user.is_authenticated:
                messages.error(request, "Pengguna harus login untuk mereview indikator.")
                return redirect(_review_url(selected_indicator.id, status_filter))

            if not has_role(request.user, *Roles.APPROVERS):
                messages.error(
                    request,
                    "Validasi/koreksi/penolakan indikator memerlukan peran Reviewer atau Admin.",
                )
                return redirect(_review_url(selected_indicator.id, status_filter))

            try:
                with transaction.atomic():
                    if action == "validate":
                        reviewed = validate_indicator(
                            indicator=selected_indicator,
                            reviewer=request.user,
                            notes=notes,
                        )
                        result = generate_signal_from_indicator(reviewed)
                        _signal_message(request, result)
                        messages.success(request, "Indikator berhasil divalidasi.")

                    elif action == "correct":
                        if not review_form.is_valid():
                            raise ValidationError("Data koreksi indikator belum valid.")

                        corrections = {
                            name: review_form.cleaned_data[name]
                            for name in (
                                "indicator_type", "disease", "location", "event_date",
                                "value", "unit", "direction", "summary", "confidence_score",
                            )
                        }
                        reviewed = correct_indicator(
                            indicator=selected_indicator,
                            reviewer=request.user,
                            corrections=corrections,
                            notes=notes,
                        )
                        result = generate_signal_from_indicator(reviewed)
                        _signal_message(request, result)
                        messages.success(request, "Koreksi indikator berhasil disimpan.")

                    elif action == "reject":
                        reject_indicator(
                            indicator=selected_indicator,
                            reviewer=request.user,
                            notes=notes,
                        )
                        messages.success(request, "Indikator berhasil ditolak.")
                    else:
                        raise ValidationError("Aksi review indikator tidak dikenali.")

            except ValidationError as exc:
                messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
            except Exception as exc:
                messages.error(request, f"Review indikator gagal: {exc}")

            return redirect(_review_url(selected_indicator.id, status_filter))

    summary = {
        "all": Indicator.objects.count(),
        "needs_review": Indicator.objects.filter(status=Indicator.Status.NEEDS_REVIEW).count(),
        "validated": Indicator.objects.filter(status=Indicator.Status.VALIDATED).count(),
        "corrected": Indicator.objects.filter(status=Indicator.Status.CORRECTED).count(),
        "rejected": Indicator.objects.filter(status=Indicator.Status.REJECTED).count(),
    }

    return render(request, "indicators/indicator_review.html", {
        "page_title": "Review Indikator",
        "active_menu": "indicator_review",
        "indicators": indicators[:100],
        "selected_indicator": selected_indicator,
        "review_form": review_form,
        "evidences": evidences,
        "review_logs": review_logs,
        "linked_signals": linked_signals,
        "status_filter": status_filter,
        "summary": summary,
    })


def _review_url(indicator_id, status_filter: str) -> str:
    return f"/indikator/review/?indicator={indicator_id}&status={status_filter}"


def _signal_message(request, result) -> None:
    if result.signal is not None:
        if result.created:
            messages.success(request, f"Signal baru terbentuk: {result.signal.code}.")
        else:
            messages.info(request, f"Indikator ditambahkan ke signal: {result.signal.code}.")
    elif result.skipped_reason:
        messages.warning(request, f"Signal belum dibentuk: {result.skipped_reason}")
