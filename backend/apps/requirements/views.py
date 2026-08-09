from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.permissions import (
    Roles,
    has_role,
    require_role_by_method,
)
from apps.articles.models import Article
from apps.assessments.models import (
    EarlyWarning,
    IntelligenceRecommendation,
    IntelligenceReportSection,
    SignalAssessment,
)
from apps.signals.models import Signal

from .forms import (
    IntelligenceRequirementForm,
    RequirementAnswerForm,
    RequirementArticleLinkForm,
    RequirementCloseForm,
    RequirementCollectionLinkForm,
    RequirementDecisionForm,
    RequirementGapResolutionForm,
    RequirementInformationGapForm,
)
from .models import (
    IntelligenceRequirement,
    RequirementArticle,
    RequirementInformationGap,
)
from .services.workspace import (
    activate_requirement,
    activation_blockers,
    answer_eligibility,
    answer_requirement,
    build_requirement_coverage,
    close_requirement,
    create_requirement,
    link_article,
    link_collection_session,
    open_information_gap,
    requirement_evidence_articles,
    resolve_information_gap,
    sync_collection_articles,
    unlink_article,
    update_requirement,
)


ACTIVE_SIGNAL_STATUSES = {
    Signal.Status.VALIDATED,
    Signal.Status.CORRECTED,
    Signal.Status.ESCALATED,
}


def _workspace_url(requirement=None, *, new=False):
    url = reverse("dashboard:intelligence-requirement")
    if new:
        return f"{url}?new=1"
    if requirement:
        return f"{url}?requirement={requirement.pk}"
    return url


def _requirements_queryset():
    return (
        IntelligenceRequirement.objects.select_related(
            "assigned_to", "created_by", "activated_by", "answered_by", "closed_by"
        )
        .prefetch_related(
            "requirement_diseases__disease",
            "requirement_locations__location",
            "keywords",
        )
        .order_by("-updated_at")
    )


def _signal_rows(requirement):
    rows = []
    relations = (
        requirement.signal_links.select_related(
            "signal__primary_disease", "signal__primary_location"
        )
        .filter(signal__status__in=ACTIVE_SIGNAL_STATUSES)
        .order_by("-is_primary", "-relevance_score", "-created_at")
    )
    for relation in relations:
        signal = relation.signal
        assessment = signal.assessments.filter(
            is_current=True,
            status=SignalAssessment.Status.COMPLETED,
        ).first()
        warning = signal.early_warnings.filter(
            is_current=True,
            status=EarlyWarning.Status.ISSUED,
        ).first()
        recommendation = (
            signal.intelligence_recommendations.filter(is_current=True)
            .exclude(
                status__in={
                    IntelligenceRecommendation.Status.CANCELED,
                    IntelligenceRecommendation.Status.SUPERSEDED,
                }
            )
            .first()
        )
        reports = IntelligenceReportSection.objects.filter(
            signal=signal
        ).select_related("report").order_by("-report__report_date")
        rows.append(
            {
                "relation": relation,
                "signal": signal,
                "assessment": assessment,
                "warning": warning,
                "recommendation": recommendation,
                "reports": reports,
            }
        )
    return rows


def _is_approver(user):
    return has_role(user, *Roles.APPROVERS)


@require_role_by_method(
    read_roles=Roles.ALL,
    write_roles=Roles.CONTRIBUTORS,
)
def intelligence_requirement_workspace(request: HttpRequest) -> HttpResponse:
    requirements = _requirements_queryset()
    selected_status = request.GET.get("status", "")
    selected_priority = request.GET.get("priority", "")
    query = request.GET.get("q", "").strip()

    if selected_status:
        requirements = requirements.filter(status=selected_status)
    if selected_priority:
        requirements = requirements.filter(priority=selected_priority)
    if query:
        requirements = requirements.filter(
            Q(code__icontains=query)
            | Q(title__icontains=query)
            | Q(question__icontains=query)
            | Q(description__icontains=query)
        ).distinct()

    is_new = request.GET.get("new") == "1"
    selected_id = request.GET.get("requirement")
    if request.method == "POST":
        selected_id = request.POST.get("requirement_id")
        is_new = request.POST.get("action") == "create"

    selected = None
    if selected_id:
        selected = get_object_or_404(_requirements_queryset(), pk=selected_id)
    elif not is_new:
        selected = requirements.first()

    requirement_form = IntelligenceRequirementForm(instance=selected)
    decision_form = RequirementDecisionForm()
    answer_form = RequirementAnswerForm()
    close_form = RequirementCloseForm()
    gap_form = RequirementInformationGapForm()
    gap_resolution_form = RequirementGapResolutionForm()
    collection_form = RequirementCollectionLinkForm(requirement=selected)
    article_form = RequirementArticleLinkForm(requirement=selected)

    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "create":
                requirement_form = IntelligenceRequirementForm(request.POST)
                if requirement_form.is_valid():
                    selected = create_requirement(
                        form=requirement_form,
                        actor=request.user,
                    )
                    messages.success(
                        request, f"Draf {selected.code} berhasil dibentuk."
                    )
                    return redirect(_workspace_url(selected))

            elif not selected:
                raise ValidationError("Kebutuhan intelijen tidak ditemukan.")

            elif action == "update":
                requirement_form = IntelligenceRequirementForm(
                    request.POST, instance=selected
                )
                if requirement_form.is_valid():
                    selected = update_requirement(
                        requirement=selected,
                        form=requirement_form,
                        actor=request.user,
                    )
                    messages.success(request, f"{selected.code} diperbarui.")
                    return redirect(_workspace_url(selected))

            elif action == "activate":
                if not _is_approver(request.user):
                    raise PermissionDenied(
                        "Aktivasi kebutuhan memerlukan peran Reviewer atau Admin."
                    )
                decision_form = RequirementDecisionForm(request.POST)
                if decision_form.is_valid():
                    activate_requirement(
                        requirement=selected,
                        actor=request.user,
                        notes=decision_form.cleaned_data["decision_notes"],
                    )
                    messages.success(request, f"{selected.code} diaktifkan.")
                    return redirect(_workspace_url(selected))

            elif action == "answer":
                if not _is_approver(request.user):
                    raise PermissionDenied(
                        "Penetapan jawaban memerlukan peran Reviewer atau Admin."
                    )
                answer_form = RequirementAnswerForm(request.POST)
                if answer_form.is_valid():
                    answer_requirement(
                        requirement=selected,
                        actor=request.user,
                        answer_summary=answer_form.cleaned_data["answer_summary"],
                        notes=answer_form.cleaned_data["decision_notes"],
                    )
                    messages.success(
                        request, f"{selected.code} dinyatakan terjawab."
                    )
                    return redirect(_workspace_url(selected))

            elif action == "close":
                if not _is_approver(request.user):
                    raise PermissionDenied(
                        "Penutupan kebutuhan memerlukan peran Reviewer atau Admin."
                    )
                close_form = RequirementCloseForm(request.POST)
                if close_form.is_valid():
                    close_requirement(
                        requirement=selected,
                        actor=request.user,
                        notes=close_form.cleaned_data["closure_notes"],
                    )
                    messages.success(request, f"{selected.code} ditutup.")
                    return redirect(_workspace_url(selected))

            elif action == "link_collection":
                collection_form = RequirementCollectionLinkForm(
                    request.POST, requirement=selected
                )
                if collection_form.is_valid():
                    link_collection_session(
                        requirement=selected,
                        session=collection_form.cleaned_data["session"],
                        actor=request.user,
                        notes=collection_form.cleaned_data["notes"],
                    )
                    messages.success(request, "Sesi koleksi berhasil ditautkan.")
                    return redirect(_workspace_url(selected))

            elif action == "sync_articles":
                count = sync_collection_articles(
                    requirement=selected, actor=request.user
                )
                messages.success(
                    request,
                    f"Sinkronisasi selesai: {count} artikel tervalidasi baru ditautkan.",
                )
                return redirect(_workspace_url(selected))

            elif action == "link_article":
                article_form = RequirementArticleLinkForm(
                    request.POST, requirement=selected
                )
                if article_form.is_valid():
                    link_article(
                        requirement=selected,
                        article=article_form.cleaned_data["article"],
                        actor=request.user,
                        reason=article_form.cleaned_data["relevance_reason"],
                    )
                    messages.success(request, "Artikel tervalidasi ditautkan.")
                    return redirect(_workspace_url(selected))

            elif action == "unlink_article":
                article = get_object_or_404(
                    Article, pk=request.POST.get("article_id")
                )
                unlink_article(
                    requirement=selected, article=article, actor=request.user
                )
                messages.success(request, "Tautan artikel dilepas.")
                return redirect(_workspace_url(selected))

            elif action == "open_gap":
                gap_form = RequirementInformationGapForm(request.POST)
                if gap_form.is_valid():
                    open_information_gap(
                        requirement=selected,
                        actor=request.user,
                        description=gap_form.cleaned_data["description"],
                        priority=gap_form.cleaned_data["priority"],
                    )
                    messages.success(request, "Kesenjangan informasi dicatat.")
                    return redirect(_workspace_url(selected))

            elif action == "resolve_gap":
                gap = get_object_or_404(
                    RequirementInformationGap,
                    pk=request.POST.get("gap_id"),
                    requirement=selected,
                )
                gap_resolution_form = RequirementGapResolutionForm(request.POST)
                if gap_resolution_form.is_valid():
                    resolve_information_gap(
                        gap=gap,
                        actor=request.user,
                        notes=gap_resolution_form.cleaned_data["resolution_notes"],
                    )
                    messages.success(request, "Kesenjangan dinyatakan terpenuhi.")
                    return redirect(_workspace_url(selected))
            else:
                raise ValidationError("Aksi workspace tidak dikenali.")

        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))

    coverage = None
    eligibility = None
    blockers = []
    signal_rows = []
    evidence_articles = []
    collection_links = []
    direct_article_links = []
    if selected:
        coverage = build_requirement_coverage(selected)
        eligibility = answer_eligibility(selected)
        blockers = activation_blockers(selected)
        signal_rows = _signal_rows(selected)
        evidence_articles = requirement_evidence_articles(selected)[:50]
        collection_links = selected.collection_links.select_related(
            "session__selected_source", "linked_by"
        ).order_by("-linked_at")
        direct_article_links = selected.article_links.select_related(
            "article__source", "linked_by"
        ).order_by("-linked_at")

    all_requirements = IntelligenceRequirement.objects.all()
    summary = {
        "total": all_requirements.count(),
        "draft": all_requirements.filter(
            status=IntelligenceRequirement.Status.DRAFT
        ).count(),
        "active": all_requirements.filter(
            status=IntelligenceRequirement.Status.ACTIVE
        ).count(),
        "answered": all_requirements.filter(
            status=IntelligenceRequirement.Status.ANSWERED
        ).count(),
        "open_gaps": RequirementInformationGap.objects.filter(
            status=RequirementInformationGap.Status.OPEN
        ).count(),
    }

    context = {
        "page_title": "Kebutuhan Intelijen",
        "active_menu": "intelligence_requirements",
        "requirements": requirements,
        "selected": selected,
        "is_new": is_new,
        "summary": summary,
        "requirement_form": requirement_form,
        "decision_form": decision_form,
        "answer_form": answer_form,
        "close_form": close_form,
        "gap_form": gap_form,
        "gap_resolution_form": gap_resolution_form,
        "collection_form": collection_form,
        "article_form": article_form,
        "coverage": coverage,
        "eligibility": eligibility,
        "activation_blockers": blockers,
        "signal_rows": signal_rows,
        "evidence_articles": evidence_articles,
        "collection_links": collection_links,
        "direct_article_links": direct_article_links,
        "status_choices": IntelligenceRequirement.Status.choices,
        "priority_choices": IntelligenceRequirement.Priority.choices,
        "selected_status": selected_status,
        "selected_priority": selected_priority,
        "query": query,
        "can_approve": _is_approver(request.user),
    }
    return render(
        request,
        "requirements/intelligence_requirement_workspace.html",
        context,
    )

