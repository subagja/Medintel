from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import (
    IntelligenceRecommendationCancelForm,
    IntelligenceRecommendationCompleteForm,
    IntelligenceRecommendationDecisionForm,
    IntelligenceRecommendationDraftForm,
    IntelligenceRecommendationProgressForm,
)
from .models import IntelligenceRecommendation, SignalAssessment
from .services import (
    IntelligenceRecommendationInput,
    approve_intelligence_recommendation,
    cancel_intelligence_recommendation,
    complete_intelligence_recommendation,
    create_intelligence_recommendation_draft,
    default_intelligence_recommendation_initial,
    evaluate_intelligence_recommendation_eligibility,
    start_intelligence_recommendation,
)


def _actor(request: HttpRequest):
    if request.user.is_authenticated:
        return request.user
    return None


def _workspace_url(assessment_id=None) -> str:
    url = reverse("dashboard:intelligence-recommendation")
    if assessment_id:
        return f"{url}?assessment={assessment_id}"
    return url


def _current_assessments():
    return (
        SignalAssessment.objects.filter(
            is_current=True,
            status=SignalAssessment.Status.COMPLETED,
        )
        .select_related(
            "signal__primary_disease",
            "signal__primary_location",
            "assessed_by",
        )
        .prefetch_related(
            "signal__histories",
            "signal__early_warnings",
        )
        .order_by("-signal__last_updated_at")
    )


def _recommendation_for(assessment):
    try:
        return assessment.intelligence_recommendation
    except ObjectDoesNotExist:
        return None


def intelligence_recommendation_workspace(
    request: HttpRequest,
) -> HttpResponse:
    assessments = list(_current_assessments())
    today = timezone.localdate()

    rows = []
    for assessment in assessments:
        recommendation = _recommendation_for(assessment)
        eligibility = evaluate_intelligence_recommendation_eligibility(
            assessment
        )
        rows.append(
            {
                "assessment": assessment,
                "recommendation": recommendation,
                "eligibility": eligibility,
                "overdue": bool(
                    recommendation
                    and recommendation.is_current
                    and recommendation.due_date
                    and recommendation.due_date < today
                    and recommendation.status
                    in {
                        IntelligenceRecommendation.Status.APPROVED,
                        IntelligenceRecommendation.Status.IN_PROGRESS,
                    }
                ),
            }
        )

    selected_id = request.GET.get("assessment")
    selected_recommendation_id = request.GET.get("recommendation")
    if request.method == "POST":
        selected_id = request.POST.get("assessment_id")
        selected_recommendation_id = None

    selected_assessment = None
    selected_recommendation = None
    if selected_recommendation_id:
        selected_recommendation = get_object_or_404(
            IntelligenceRecommendation.objects.select_related(
                "assessment__signal__primary_disease",
                "assessment__signal__primary_location",
                "early_warning",
            ),
            pk=selected_recommendation_id,
        )
        selected_assessment = selected_recommendation.assessment
    elif selected_id:
        selected_assessment = get_object_or_404(
            _current_assessments(),
            pk=selected_id,
        )
    elif rows:
        ready_row = next(
            (
                row
                for row in rows
                if row["eligibility"].is_eligible
            ),
            None,
        )
        selected_assessment = (
            ready_row["assessment"] if ready_row else rows[0]["assessment"]
        )

    eligibility = None
    draft_form = None
    decision_form = IntelligenceRecommendationDecisionForm()
    progress_form = IntelligenceRecommendationProgressForm()
    complete_form = IntelligenceRecommendationCompleteForm()
    cancel_form = IntelligenceRecommendationCancelForm()

    if selected_assessment:
        if selected_recommendation is None:
            selected_recommendation = _recommendation_for(
                selected_assessment
            )
        eligibility = evaluate_intelligence_recommendation_eligibility(
            selected_assessment
        )
        draft_form = IntelligenceRecommendationDraftForm(
            initial=default_intelligence_recommendation_initial(
                selected_assessment
            )
        )

        if request.method == "POST":
            action = request.POST.get("action")

            if action == "create_draft":
                draft_form = IntelligenceRecommendationDraftForm(
                    request.POST
                )
                if draft_form.is_valid():
                    try:
                        recommendation = (
                            create_intelligence_recommendation_draft(
                                assessment=selected_assessment,
                                analyst=_actor(request),
                                recommendation_input=(
                                    IntelligenceRecommendationInput(
                                        title=draft_form.cleaned_data[
                                            "title"
                                        ],
                                        situation_summary=(
                                            draft_form.cleaned_data[
                                                "situation_summary"
                                            ]
                                        ),
                                        objective=draft_form.cleaned_data[
                                            "objective"
                                        ],
                                        recommended_action=(
                                            draft_form.cleaned_data[
                                                "recommended_action"
                                            ]
                                        ),
                                        action_category=(
                                            draft_form.cleaned_data[
                                                "action_category"
                                            ]
                                        ),
                                        urgency=draft_form.cleaned_data[
                                            "urgency"
                                        ],
                                        target_unit=draft_form.cleaned_data[
                                            "target_unit"
                                        ],
                                        due_date=draft_form.cleaned_data[
                                            "due_date"
                                        ],
                                        success_indicators=(
                                            draft_form.cleaned_data[
                                                "success_indicators"
                                            ]
                                        ),
                                        assumptions=draft_form.cleaned_data[
                                            "assumptions"
                                        ],
                                        information_gaps=(
                                            draft_form.cleaned_data[
                                                "information_gaps"
                                            ]
                                        ),
                                    )
                                ),
                            )
                        )
                    except ValidationError as exc:
                        draft_form.add_error(None, exc)
                        messages.error(request, " ".join(exc.messages))
                    else:
                        messages.success(
                            request,
                            f"Draf {recommendation.code} berhasil dibentuk.",
                        )
                        return redirect(
                            _workspace_url(selected_assessment.pk)
                        )

            elif action in {
                "approve",
                "start",
                "complete",
                "cancel",
            }:
                recommendation = get_object_or_404(
                    IntelligenceRecommendation,
                    pk=request.POST.get("recommendation_id"),
                    assessment=selected_assessment,
                )

                if action == "approve":
                    decision_form = (
                        IntelligenceRecommendationDecisionForm(request.POST)
                    )
                    if decision_form.is_valid():
                        try:
                            approve_intelligence_recommendation(
                                recommendation=recommendation,
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
                                f"{recommendation.code} berhasil ditetapkan.",
                            )
                            return redirect(
                                _workspace_url(selected_assessment.pk)
                            )

                elif action == "start":
                    progress_form = (
                        IntelligenceRecommendationProgressForm(request.POST)
                    )
                    if progress_form.is_valid():
                        try:
                            start_intelligence_recommendation(
                                recommendation=recommendation,
                                analyst=_actor(request),
                                notes=progress_form.cleaned_data[
                                    "progress_notes"
                                ],
                            )
                        except ValidationError as exc:
                            progress_form.add_error(None, exc)
                        else:
                            messages.success(
                                request,
                                (
                                    f"Tindak lanjut {recommendation.code} "
                                    "berhasil dimulai."
                                ),
                            )
                            return redirect(
                                _workspace_url(selected_assessment.pk)
                            )

                elif action == "complete":
                    complete_form = (
                        IntelligenceRecommendationCompleteForm(request.POST)
                    )
                    if complete_form.is_valid():
                        try:
                            complete_intelligence_recommendation(
                                recommendation=recommendation,
                                analyst=_actor(request),
                                notes=complete_form.cleaned_data[
                                    "completion_notes"
                                ],
                            )
                        except ValidationError as exc:
                            complete_form.add_error(None, exc)
                        else:
                            messages.success(
                                request,
                                f"{recommendation.code} berhasil diselesaikan.",
                            )
                            return redirect(
                                _workspace_url(selected_assessment.pk)
                            )

                elif action == "cancel":
                    cancel_form = (
                        IntelligenceRecommendationCancelForm(request.POST)
                    )
                    if cancel_form.is_valid():
                        try:
                            cancel_intelligence_recommendation(
                                recommendation=recommendation,
                                analyst=_actor(request),
                                notes=cancel_form.cleaned_data[
                                    "cancellation_reason"
                                ],
                            )
                        except ValidationError as exc:
                            cancel_form.add_error(None, exc)
                        else:
                            messages.success(
                                request,
                                f"{recommendation.code} berhasil dibatalkan.",
                            )
                            return redirect(
                                _workspace_url(selected_assessment.pk)
                            )

    current_recommendations = IntelligenceRecommendation.objects.filter(
        is_current=True
    )
    summary = {
        "assessments": len(assessments),
        "ready": sum(
            1 for row in rows if row["eligibility"].is_eligible
        ),
        "drafts": current_recommendations.filter(
            status=IntelligenceRecommendation.Status.DRAFT
        ).count(),
        "active": current_recommendations.filter(
            status__in=[
                IntelligenceRecommendation.Status.APPROVED,
                IntelligenceRecommendation.Status.IN_PROGRESS,
            ]
        ).count(),
        "overdue": current_recommendations.filter(
            status__in=[
                IntelligenceRecommendation.Status.APPROVED,
                IntelligenceRecommendation.Status.IN_PROGRESS,
            ],
            due_date__lt=today,
        ).count(),
    }

    selected_due_state = ""
    if selected_recommendation and selected_recommendation.due_date:
        if selected_recommendation.due_date < today:
            selected_due_state = "overdue"
        elif selected_recommendation.due_date <= today + timedelta(days=3):
            selected_due_state = "soon"
        else:
            selected_due_state = "scheduled"

    recent_recommendations = list(
        IntelligenceRecommendation.objects.select_related(
            "signal__primary_disease",
            "signal__primary_location",
            "approved_by",
        ).order_by("-created_at")[:10]
    )

    return render(
        request,
        "assessments/intelligence_recommendation_workspace.html",
        {
            "page_title": "Rekomendasi Intelijen",
            "active_menu": "recommendations",
            "rows": rows,
            "selected_assessment": selected_assessment,
            "selected_recommendation": selected_recommendation,
            "selected_due_state": selected_due_state,
            "eligibility": eligibility,
            "draft_form": draft_form,
            "decision_form": decision_form,
            "progress_form": progress_form,
            "complete_form": complete_form,
            "cancel_form": cancel_form,
            "summary": summary,
            "recent_recommendations": recent_recommendations,
        },
    )
