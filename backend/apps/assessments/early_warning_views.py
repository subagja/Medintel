from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.permissions import Roles, has_role, require_role_by_method

from .forms import EarlyWarningCloseForm, EarlyWarningIssueForm
from .models import EarlyWarning, SignalAssessment
from .services import (
    EarlyWarningInput,
    close_early_warning,
    default_early_warning_initial,
    evaluate_early_warning_eligibility,
    issue_early_warning,
)


def _actor(request: HttpRequest):
    if request.user.is_authenticated:
        return request.user
    return None


def _workspace_url(assessment_id=None) -> str:
    url = reverse("dashboard:early-warning")
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
            "signal__signal_articles__article__source",
        )
        .order_by("-signal__last_updated_at")
    )


def _warning_for(assessment):
    try:
        return assessment.early_warning
    except ObjectDoesNotExist:
        return None


@require_role_by_method(
    read_roles=Roles.ALL,
    write_roles=Roles.CONTRIBUTORS,
)
def early_warning_workspace(request: HttpRequest) -> HttpResponse:
    assessments = list(_current_assessments())
    rows = []
    for assessment in assessments:
        rows.append(
            {
                "assessment": assessment,
                "warning": _warning_for(assessment),
                "eligibility": evaluate_early_warning_eligibility(
                    assessment
                ),
            }
        )

    selected_id = request.GET.get("assessment")
    if request.method == "POST":
        selected_id = request.POST.get("assessment_id")

    selected_assessment = None
    if selected_id:
        selected_assessment = get_object_or_404(
            _current_assessments(),
            pk=selected_id,
        )
    elif rows:
        eligible_row = next(
            (
                row
                for row in rows
                if row["eligibility"].is_eligible
            ),
            None,
        )
        selected_assessment = (
            eligible_row["assessment"] if eligible_row else rows[0]["assessment"]
        )

    selected_warning = None
    eligibility = None
    issue_form = None
    close_form = None

    if selected_assessment:
        selected_warning = _warning_for(selected_assessment)
        eligibility = evaluate_early_warning_eligibility(
            selected_assessment
        )
        issue_form = EarlyWarningIssueForm(
            initial=default_early_warning_initial(selected_assessment)
        )
        close_form = EarlyWarningCloseForm()

        if request.method == "POST":
            action = request.POST.get("action")

            if action == "issue_warning":
                if not has_role(request.user, *Roles.APPROVERS):
                    messages.error(
                        request,
                        "Penerbitan peringatan dini memerlukan peran Reviewer atau Admin.",
                    )
                    issue_form = EarlyWarningIssueForm(request.POST)
                elif (issue_form := EarlyWarningIssueForm(request.POST)).is_valid():
                    try:
                        warning = issue_early_warning(
                            assessment=selected_assessment,
                            analyst=_actor(request),
                            warning_input=EarlyWarningInput(
                                title=issue_form.cleaned_data["title"],
                                summary=issue_form.cleaned_data["summary"],
                                recommended_actions=(
                                    issue_form.cleaned_data[
                                        "recommended_actions"
                                    ]
                                ),
                                decision_notes=issue_form.cleaned_data[
                                    "decision_notes"
                                ],
                            ),
                        )
                    except ValidationError as exc:
                        issue_form.add_error(None, exc)
                    else:
                        messages.success(
                            request,
                            f"{warning.code} berhasil diterbitkan.",
                        )
                        return redirect(
                            _workspace_url(selected_assessment.pk)
                        )

            elif action == "close_warning":
                close_form = EarlyWarningCloseForm(request.POST)
                warning = get_object_or_404(
                    EarlyWarning,
                    pk=request.POST.get("warning_id"),
                    assessment=selected_assessment,
                    is_current=True,
                )
                if not has_role(request.user, *Roles.APPROVERS):
                    messages.error(
                        request,
                        "Penutupan peringatan dini memerlukan peran Reviewer atau Admin.",
                    )
                elif close_form.is_valid():
                    try:
                        close_early_warning(
                            warning=warning,
                            analyst=_actor(request),
                            notes=close_form.cleaned_data[
                                "closure_notes"
                            ],
                        )
                    except ValidationError as exc:
                        close_form.add_error(None, exc)
                    else:
                        messages.success(
                            request,
                            f"{warning.code} berhasil ditutup.",
                        )
                        return redirect(
                            _workspace_url(selected_assessment.pk)
                        )

    active_warnings = EarlyWarning.objects.filter(
        is_current=True,
        status=EarlyWarning.Status.ISSUED,
    )
    summary = {
        "assessments": len(assessments),
        "eligible": sum(
            1 for row in rows if row["eligibility"].is_eligible
        ),
        "active": active_warnings.count(),
        "high": active_warnings.filter(
            level__in=[
                EarlyWarning.Level.HIGH,
                EarlyWarning.Level.CRITICAL,
            ]
        ).count(),
    }

    recent_warnings = list(
        EarlyWarning.objects.select_related(
            "signal__primary_disease",
            "signal__primary_location",
            "issued_by",
        ).order_by("-issued_at")[:10]
    )

    return render(
        request,
        "assessments/early_warning_workspace.html",
        {
            "page_title": "Peringatan Dini",
            "active_menu": "early_warning",
            "rows": rows,
            "selected_assessment": selected_assessment,
            "selected_warning": selected_warning,
            "eligibility": eligibility,
            "issue_form": issue_form,
            "close_form": close_form,
            "summary": summary,
            "recent_warnings": recent_warnings,
        },
    )
