from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.permissions import Roles, require_role

from apps.articles.models import Article
from apps.assessments.models import ArticleValidationAssessment

from .forms import SignalFormationForm, SignalReviewForm
from .models import Signal
from .services import (
    correct_signal,
    evaluate_article_signal_candidate,
    form_signal_from_article,
    reject_signal,
    start_signal_review,
    validate_signal,
)


def _candidate_articles():
    return (
        Article.objects.filter(
            validation_assessment__validation_status=(
                ArticleValidationAssessment.ValidationStatus.VALIDATED
            ),
            validation_assessment__evaluated_by__isnull=False,
        )
        .exclude(
            validation_assessment__source_reliability=(
                ArticleValidationAssessment.SourceReliability.F
            )
        )
        .exclude(
            validation_assessment__information_credibility=(
                ArticleValidationAssessment
                .InformationCredibility
                .UNASSESSABLE
            )
        )
        .exclude(signal_links__isnull=False)
        .select_related("source", "validation_assessment")
        .prefetch_related(
            "article_diseases__disease",
            "article_locations__location",
            "facts__disease",
            "facts__location",
        )
        .distinct()
        .order_by("-published_at", "-crawled_at")
    )


def _signals():
    return (
        Signal.objects.select_related(
            "primary_disease",
            "primary_location",
            "assigned_to",
            "validated_by",
        )
        .prefetch_related(
            "signal_articles__article__source",
            "signal_indicators__indicator__indicator_type",
            "signal_requirements__requirement",
            "histories__changed_by",
        )
        .order_by("-last_updated_at")
    )


def _workspace_url(*, mode: str, article_id=None, signal_id=None) -> str:
    url = reverse("dashboard:signal-workspace")
    parameters = [f"mode={mode}"]
    if article_id:
        parameters.append(f"article={article_id}")
    if signal_id:
        parameters.append(f"signal={signal_id}")
    return f"{url}?{'&'.join(parameters)}"


def _error_text(exc: Exception) -> str:
    return " ".join(getattr(exc, "messages", [str(exc)]))


@require_role(*Roles.ALL)
def signal_workspace(request: HttpRequest) -> HttpResponse:
    mode = request.GET.get("mode", "candidates")
    if mode not in {"candidates", "signals"}:
        mode = "candidates"

    candidate_rows = []
    for article in _candidate_articles()[:100]:
        candidate = evaluate_article_signal_candidate(article)
        if candidate.is_ready:
            candidate_rows.append(candidate)

    signal_rows = _signals()[:100]

    selected_candidate = None
    selected_signal = None
    formation_form = None
    review_form = None

    selected_article_id = (
        request.POST.get("article_id") or request.GET.get("article")
    )
    selected_signal_id = (
        request.POST.get("signal_id") or request.GET.get("signal")
    )

    if mode == "candidates" and candidate_rows:
        selected_candidate = next(
            (
                item
                for item in candidate_rows
                if str(item.article.pk) == str(selected_article_id)
            ),
            candidate_rows[0],
        )
        formation_form = SignalFormationForm(
            request.POST or None,
            article=selected_candidate.article,
            candidate=selected_candidate,
        )

    if mode == "signals" and signal_rows:
        selected_signal = next(
            (
                item
                for item in signal_rows
                if str(item.pk) == str(selected_signal_id)
            ),
            signal_rows[0],
        )
        review_form = SignalReviewForm(
            request.POST or None,
            instance=selected_signal,
        )

    if request.method == "POST":
        action = request.POST.get("action", "")

        if not request.user.is_authenticated:
            messages.error(request, "Pengguna harus login untuk memproses sinyal.")
            return redirect(_workspace_url(mode=mode))

        if action == "form_signal":
            article = get_object_or_404(
                Article.objects.select_related("source"),
                pk=request.POST.get("article_id"),
            )
            candidate = evaluate_article_signal_candidate(article)
            formation_form = SignalFormationForm(
                request.POST,
                article=article,
                candidate=candidate,
            )
            if formation_form.is_valid():
                try:
                    result = form_signal_from_article(
                        article=article,
                        analyst=request.user,
                        title=formation_form.cleaned_data["title"],
                        summary=formation_form.cleaned_data["summary"],
                        notes=formation_form.cleaned_data["formation_notes"],
                    )
                except ValidationError as exc:
                    formation_form.add_error(None, exc)
                else:
                    if result.created:
                        messages.success(
                            request,
                            f"Kandidat sinyal {result.signal.code} berhasil dibentuk.",
                        )
                    else:
                        messages.info(
                            request,
                            f"Artikel digabungkan ke sinyal {result.signal.code}.",
                        )
                    return redirect(
                        _workspace_url(
                            mode="signals",
                            signal_id=result.signal.pk,
                        )
                    )

            selected_candidate = candidate
            mode = "candidates"

        elif action in {"validate_signal", "reject_signal"}:
            signal = get_object_or_404(Signal, pk=request.POST.get("signal_id"))
            review_form = SignalReviewForm(request.POST, instance=signal)
            mode = "signals"
            selected_signal = signal

            if review_form.is_valid():
                notes = review_form.cleaned_data["review_notes"]
                try:
                    if action == "reject_signal":
                        reject_signal(
                            signal=signal,
                            reviewer=request.user,
                            notes=notes,
                        )
                        messages.success(
                            request,
                            f"Sinyal {signal.code} berhasil ditolak.",
                        )
                    else:
                        _confirm_signal(
                            signal=signal,
                            reviewer=request.user,
                            form=review_form,
                            notes=notes,
                        )
                        messages.success(
                            request,
                            f"Sinyal {signal.code} berhasil dikonfirmasi analis.",
                        )
                except ValidationError as exc:
                    review_form.add_error(None, exc)
                else:
                    return redirect(
                        _workspace_url(mode="signals", signal_id=signal.pk)
                    )

    summary = {
        "candidates": len(candidate_rows),
        "needs_review": Signal.objects.filter(
            status__in=[Signal.Status.NEEDS_REVIEW, Signal.Status.UNDER_REVIEW]
        ).count(),
        "validated": Signal.objects.filter(
            status__in=[Signal.Status.VALIDATED, Signal.Status.CORRECTED]
        ).count(),
        "escalated": Signal.objects.filter(status=Signal.Status.ESCALATED).count(),
    }

    return render(
        request,
        "signals/signal_workspace.html",
        {
            "page_title": "Sinyal Intelijen",
            "active_menu": "signals",
            "mode": mode,
            "candidate_rows": candidate_rows,
            "signal_rows": signal_rows,
            "selected_candidate": selected_candidate,
            "selected_signal": selected_signal,
            "formation_form": formation_form,
            "review_form": review_form,
            "summary": summary,
        },
    )


@transaction.atomic
def _confirm_signal(*, signal: Signal, reviewer, form, notes: str) -> Signal:
    if signal.status == Signal.Status.NEEDS_REVIEW:
        start_signal_review(signal=signal, reviewer=reviewer, notes=notes)
        signal.refresh_from_db()

    correction_fields = [
        "title",
        "summary",
        "event_start_date",
        "event_end_date",
        "priority_level",
        "confidence_level",
        "analyst_judgement",
        "implication",
        "recommended_action",
        "information_gaps",
    ]
    corrections = {
        field_name: form.cleaned_data[field_name]
        for field_name in correction_fields
        if getattr(signal, field_name) != form.cleaned_data[field_name]
    }

    if corrections:
        correct_signal(
            signal=signal,
            reviewer=reviewer,
            corrections=corrections,
            notes=notes,
        )
        signal.refresh_from_db()

    if signal.status == Signal.Status.VALIDATED:
        raise ValidationError("Sinyal sudah tervalidasi dan tidak berubah.")

    return validate_signal(
        signal=signal,
        reviewer=reviewer,
        judgement=form.cleaned_data["analyst_judgement"],
        implication=form.cleaned_data["implication"],
        recommended_action=form.cleaned_data["recommended_action"],
        information_gaps=form.cleaned_data["information_gaps"],
        notes=notes,
    )
