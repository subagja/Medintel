from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.permissions import Roles, require_role

from apps.signals.models import Signal

from .forms import (
    ApplyAssessmentRecommendationForm,
    SignalThreatAssessmentForm,
)
from .models import SignalAssessment
from .services import (
    SignalAssessmentInput,
    apply_assessment_recommendation,
    assessment_recommendation_is_applied,
    create_signal_assessment,
    sync_signal_evaluations_from_information_balance,
)


ASSESSABLE_SIGNAL_STATUSES = (
    Signal.Status.VALIDATED,
    Signal.Status.CORRECTED,
    Signal.Status.ESCALATED,
)

ASSESSMENT_FORM_FIELDS = (
    "urgency_score",
    "impact_score",
    "geographic_scope_score",
    "development_speed_score",
    "vulnerability_score",
    "information_completeness_score",
    "evidence_consistency_score",
    "analytical_judgement",
    "implications",
    "recommended_actions",
    "assumptions",
    "limitations",
)


def _workspace_url(signal_id=None) -> str:
    url = reverse("dashboard:threat-assessment")
    if signal_id:
        return f"{url}?signal={signal_id}"
    return url


def _actor(request: HttpRequest):
    if request.user.is_authenticated:
        return request.user
    return None


def _assessable_signals():
    return (
        Signal.objects.filter(status__in=ASSESSABLE_SIGNAL_STATUSES)
        .select_related("primary_disease", "primary_location")
        .prefetch_related(
            "assessments",
            "signal_articles__article__source",
            "signal_articles__article__validation_assessment",
            "signal_indicators__indicator__indicator_type",
        )
        .order_by("-last_updated_at")
    )


def _assessment_initial(signal: Signal, assessment):
    if assessment:
        return {
            field_name: getattr(assessment, field_name)
            for field_name in ASSESSMENT_FORM_FIELDS
        }

    return {
        "analytical_judgement": signal.analyst_judgement,
        "implications": signal.implication,
        "recommended_actions": signal.recommended_action,
        "limitations": signal.information_gaps,
    }


def _create_assessment(request, signal, form):
    actor = _actor(request)
    sync_signal_evaluations_from_information_balance(
        signal=signal,
        evaluator=actor,
    )
    cleaned = form.cleaned_data
    return create_signal_assessment(
        signal=signal,
        assessor=actor,
        assessment_input=SignalAssessmentInput(
            urgency_score=cleaned["urgency_score"],
            impact_score=cleaned["impact_score"],
            geographic_scope_score=cleaned[
                "geographic_scope_score"
            ],
            development_speed_score=cleaned[
                "development_speed_score"
            ],
            vulnerability_score=cleaned["vulnerability_score"],
            information_completeness_score=cleaned[
                "information_completeness_score"
            ],
            evidence_consistency_score=cleaned[
                "evidence_consistency_score"
            ],
            analytical_judgement=cleaned["analytical_judgement"],
            implications=cleaned["implications"],
            recommended_actions=cleaned["recommended_actions"],
            assumptions=cleaned["assumptions"],
            limitations=cleaned["limitations"],
        ),
    )


@require_role(*Roles.ALL)
def threat_assessment_workspace(
    request: HttpRequest,
) -> HttpResponse:
    signals = list(_assessable_signals())
    selected_signal = None
    assessment_form = None
    decision_form = None

    selected_id = request.GET.get("signal")
    if request.method == "POST":
        selected_id = request.POST.get("signal_id")

    if selected_id:
        selected_signal = get_object_or_404(
            _assessable_signals(),
            pk=selected_id,
        )
    elif signals:
        selected_signal = signals[0]

    if selected_signal:
        current_assessment = (
            selected_signal.assessments.filter(is_current=True)
            .order_by("-version")
            .first()
        )
        assessment_form = SignalThreatAssessmentForm(
            initial=_assessment_initial(
                selected_signal,
                current_assessment,
            )
        )
        decision_form = ApplyAssessmentRecommendationForm()

        if request.method == "POST":
            action = request.POST.get("action")

            if action == "create_assessment":
                assessment_form = SignalThreatAssessmentForm(
                    request.POST
                )
                if assessment_form.is_valid():
                    try:
                        assessment = _create_assessment(
                            request,
                            selected_signal,
                            assessment_form,
                        )
                    except ValidationError as exc:
                        assessment_form.add_error(None, exc)
                    else:
                        messages.success(
                            request,
                            (
                                f"Assessment v{assessment.version} untuk "
                                f"{selected_signal.code} berhasil dibuat."
                            ),
                        )
                        return redirect(
                            _workspace_url(selected_signal.pk)
                        )

            elif action == "apply_recommendation":
                decision_form = ApplyAssessmentRecommendationForm(
                    request.POST
                )
                assessment = get_object_or_404(
                    SignalAssessment,
                    pk=request.POST.get("assessment_id"),
                    signal=selected_signal,
                    is_current=True,
                )
                if decision_form.is_valid():
                    try:
                        apply_assessment_recommendation(
                            assessment=assessment,
                            analyst=_actor(request),
                            notes=decision_form.cleaned_data[
                                "decision_notes"
                            ],
                        )
                    except ValidationError as exc:
                        decision_form.add_error(None, exc)
                    else:
                        messages.success(
                            request,
                            (
                                "Rekomendasi assessment diterapkan setelah "
                                "konfirmasi analis."
                            ),
                        )
                        return redirect(
                            _workspace_url(selected_signal.pk)
                        )

        current_assessment = (
            selected_signal.assessments.filter(is_current=True)
            .order_by("-version")
            .first()
        )
        assessment_history = list(
            selected_signal.assessments.order_by("-version")[:10]
        )
        recommendation_applied = (
            assessment_recommendation_is_applied(current_assessment)
            if current_assessment
            else False
        )
    else:
        current_assessment = None
        assessment_history = []
        recommendation_applied = False

    assessed_signal_ids = {
        assessment.signal_id
        for assessment in SignalAssessment.objects.filter(
            is_current=True,
            signal__status__in=ASSESSABLE_SIGNAL_STATUSES,
        ).only("signal_id")
    }
    summary = {
        "assessable": len(signals),
        "not_assessed": sum(
            1 for signal in signals if signal.pk not in assessed_signal_ids
        ),
        "assessed": len(assessed_signal_ids),
        "high_priority": SignalAssessment.objects.filter(
            is_current=True,
            signal__status__in=ASSESSABLE_SIGNAL_STATUSES,
            recommended_priority__in=[
                SignalAssessment.RecommendedPriority.HIGH,
                SignalAssessment.RecommendedPriority.CRITICAL,
            ],
        ).count(),
    }

    return render(
        request,
        "assessments/threat_assessment_workspace.html",
        {
            "page_title": "Assessment Ancaman",
            "active_menu": "assessment",
            "signals": signals,
            "selected_signal": selected_signal,
            "current_assessment": current_assessment,
            "assessment_history": assessment_history,
            "assessment_form": assessment_form,
            "decision_form": decision_form,
            "recommendation_applied": recommendation_applied,
            "summary": summary,
        },
    )
