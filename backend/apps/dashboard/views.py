from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.articles.models import Article
from apps.assessments.forms import ArticleValidationAssessmentForm
from apps.assessments.models import (
    ArticleValidationAssessment,
    ArticleValidationHistory,
)


def dashboard_overview(request: HttpRequest) -> HttpResponse:
    context = {
        "page_title": "Dashboard Ringkasan",
        "active_menu": "dashboard",
    }

    return render(
        request,
        "dashboard/index.html",
        context,
    )


def article_validation(request: HttpRequest) -> HttpResponse:
    articles = (
        Article.objects
        .select_related("source")
        .prefetch_related(
            "diseases",
            "locations",
        )
        .order_by(
            "-published_at",
            "-crawled_at",
        )
    )

    selected_article_id = (
        request.POST.get("article_id")
        or request.GET.get("article")
    )

    if selected_article_id:
        selected_article = get_object_or_404(
            articles,
            id=selected_article_id,
        )
    else:
        selected_article = articles.first()

    assessment = None
    form = None
    assessment_history = []

    if selected_article is not None:
        assessment, _ = (
            ArticleValidationAssessment.objects.get_or_create(
                article=selected_article,
                defaults={
                    "validation_status": (
                        ArticleValidationAssessment
                        .ValidationStatus
                        .PENDING
                    ),
                    "source_reliability": (
                        ArticleValidationAssessment
                        .SourceReliability
                        .F
                    ),
                    "information_credibility": (
                        ArticleValidationAssessment
                        .InformationCredibility
                        .UNASSESSABLE
                    ),
                },
            )
        )

        if request.method == "POST":
            # Simpan nilai lama sebelum ModelForm memproses instance.
            previous_values = {
                "status": assessment.validation_status,
                "source_reliability": (
                    assessment.source_reliability
                ),
                "information_credibility": (
                    assessment.information_credibility
                ),
                "assessment_notes": assessment.assessment_notes,
                "relevance_notes": assessment.relevance_notes,
            }

            form = ArticleValidationAssessmentForm(
                request.POST,
                instance=assessment,
            )

            if form.is_valid():
                with transaction.atomic():
                    saved_assessment = form.save(commit=False)

                    if request.user.is_authenticated:
                        saved_assessment.evaluated_by = request.user

                    saved_assessment.save()

                    has_changed = _assessment_has_changed(
                        previous_values=previous_values,
                        assessment=saved_assessment,
                    )

                    if has_changed:
                        ArticleValidationHistory.objects.create(
                            assessment=saved_assessment,
                            previous_status=(
                                previous_values["status"]
                            ),
                            new_status=(
                                saved_assessment.validation_status
                            ),
                            previous_source_reliability=(
                                previous_values[
                                    "source_reliability"
                                ]
                            ),
                            new_source_reliability=(
                                saved_assessment.source_reliability
                            ),
                            previous_information_credibility=(
                                previous_values[
                                    "information_credibility"
                                ]
                            ),
                            new_information_credibility=(
                                saved_assessment
                                .information_credibility
                            ),
                            change_notes=(
                                saved_assessment.assessment_notes
                            ),
                            changed_by=(
                                request.user
                                if request.user.is_authenticated
                                else None
                            ),
                        )

                    _synchronize_article_status(
                        article=selected_article,
                        assessment=saved_assessment,
                    )

                if has_changed:
                    messages.success(
                        request,
                        (
                            "Validasi artikel berhasil disimpan "
                            f"dengan nilai "
                            f"{saved_assessment.admiralty_code}."
                        ),
                    )
                else:
                    messages.info(
                        request,
                        "Tidak ada perubahan pada penilaian artikel.",
                    )

                return redirect(
                    "dashboard:article-validation",
                    article=selected_article.id,
                )

        else:
            form = ArticleValidationAssessmentForm(
                instance=assessment,
            )

        assessment_history = (
            assessment.history
            .select_related("changed_by")
            .all()
        )

    total_articles = articles.count()

    validated_count = (
        ArticleValidationAssessment.objects.filter(
            validation_status=(
                ArticleValidationAssessment
                .ValidationStatus
                .VALIDATED
            )
        )
        .count()
    )

    rejected_count = (
        ArticleValidationAssessment.objects.filter(
            validation_status=(
                ArticleValidationAssessment
                .ValidationStatus
                .REJECTED
            )
        )
        .count()
    )

    # Termasuk artikel yang belum memiliki assessment.
    pending_count = max(
        total_articles - validated_count - rejected_count,
        0,
    )

    summary = {
        "total": total_articles,
        "pending": pending_count,
        "validated": validated_count,
        "rejected": rejected_count,
    }

    context = {
        "page_title": "Validasi Artikel",
        "active_menu": "article_validation",
        "articles": articles[:50],
        "selected_article": selected_article,
        "assessment": assessment,
        "assessment_form": form,
        "assessment_history": assessment_history,
        "summary": summary,
    }

    return render(
        request,
        "dashboard/article_validation.html",
        context,
    )


def _assessment_has_changed(
    previous_values: dict,
    assessment: ArticleValidationAssessment,
) -> bool:
    return any(
        [
            (
                previous_values["status"]
                != assessment.validation_status
            ),
            (
                previous_values["source_reliability"]
                != assessment.source_reliability
            ),
            (
                previous_values["information_credibility"]
                != assessment.information_credibility
            ),
            (
                previous_values["assessment_notes"]
                != assessment.assessment_notes
            ),
            (
                previous_values["relevance_notes"]
                != assessment.relevance_notes
            ),
        ]
    )


def _synchronize_article_status(
    article: Article,
    assessment: ArticleValidationAssessment,
) -> None:
    status_mapping = {
        (
            ArticleValidationAssessment
            .ValidationStatus
            .PENDING
        ): Article.ProcessingStatus.NEW,

        (
            ArticleValidationAssessment
            .ValidationStatus
            .VALIDATED
        ): Article.ProcessingStatus.VALIDATED,

        (
            ArticleValidationAssessment
            .ValidationStatus
            .REJECTED
        ): Article.ProcessingStatus.REJECTED,
    }

    new_processing_status = status_mapping[
        assessment.validation_status
    ]

    new_rejection_reason = ""

    if (
        assessment.validation_status
        == ArticleValidationAssessment
        .ValidationStatus
        .REJECTED
    ):
        new_rejection_reason = assessment.relevance_notes

    article_changed = (
        article.processing_status != new_processing_status
        or article.rejection_reason != new_rejection_reason
    )

    if not article_changed:
        return

    article.processing_status = new_processing_status
    article.rejection_reason = new_rejection_reason

    article.save(
        update_fields=[
            "processing_status",
            "rejection_reason",
            "updated_at",
        ]
    )