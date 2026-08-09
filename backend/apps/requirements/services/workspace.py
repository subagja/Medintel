from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.articles.models import Article
from apps.assessments.models import (
    ArticleValidationAssessment,
    EarlyWarning,
    IntelligenceRecommendation,
    IntelligenceReportSection,
    SignalAssessment,
)
from apps.signals.models import Signal
from apps.indicators.models import Indicator
from apps.indicators.services import indicator_is_eligible_for_signal

from ..models import (
    IntelligenceRequirement,
    RequirementArticle,
    RequirementCollectionSession,
    RequirementDisease,
    RequirementHistory,
    RequirementInformationGap,
    RequirementIndicator,
    RequirementKeyword,
    RequirementLocation,
)
from .matching import (
    calculate_requirement_match,
    requirement_is_active_on_date,
)


ACTIVE_SIGNAL_STATUSES = {
    Signal.Status.VALIDATED,
    Signal.Status.CORRECTED,
    Signal.Status.ESCALATED,
}


@dataclass(frozen=True)
class RequirementAnswerEligibility:
    is_eligible: bool
    blockers: tuple[str, ...] = ()


@dataclass
class RequirementCoverage:
    collection_sessions: int = 0
    articles: int = 0
    indicators: int = 0
    signals: int = 0
    assessments: int = 0
    warnings: int = 0
    recommendations: int = 0
    reports: int = 0
    open_gaps: int = 0
    system_gaps: list[str] = field(default_factory=list)


def _next_requirement_code() -> str:
    year = timezone.localdate().year
    prefix = f"IR-{year}-"
    latest = (
        IntelligenceRequirement.objects.select_for_update()
        .filter(code__startswith=prefix)
        .order_by("-code")
        .values_list("code", flat=True)
        .first()
    )
    sequence = 1
    if latest:
        try:
            sequence = int(latest.rsplit("-", 1)[1]) + 1
        except (TypeError, ValueError):
            sequence = 1
    code = f"{prefix}{sequence:04d}"
    while IntelligenceRequirement.objects.filter(code=code).exists():
        sequence += 1
        code = f"{prefix}{sequence:04d}"
    return code


def _history(
    requirement,
    *,
    action,
    actor,
    from_status="",
    notes="",
    metadata=None,
):
    return RequirementHistory.objects.create(
        requirement=requirement,
        action=action,
        from_status=from_status,
        to_status=requirement.status,
        notes=notes,
        metadata=metadata or {},
        changed_by=actor,
    )


def _ensure_requirement_editable(requirement):
    if not requirement.is_editable:
        raise ValidationError(
            "Lineage kebutuhan yang telah Terjawab atau Ditutup tidak dapat diubah."
        )


def _sync_targets(requirement, *, diseases, locations, keywords):
    RequirementDisease.objects.filter(requirement=requirement).delete()
    for index, disease in enumerate(diseases):
        RequirementDisease.objects.create(
            requirement=requirement,
            disease=disease,
            is_primary=index == 0,
        )

    RequirementLocation.objects.filter(requirement=requirement).delete()
    for index, location in enumerate(locations):
        RequirementLocation.objects.create(
            requirement=requirement,
            location=location,
            include_descendants=True,
            is_primary=index == 0,
        )

    requirement.keywords.filter(
        keyword_type=RequirementKeyword.KeywordType.GENERAL
    ).delete()
    RequirementKeyword.objects.bulk_create(
        [
            RequirementKeyword(
                requirement=requirement,
                keyword=keyword,
                keyword_type=RequirementKeyword.KeywordType.GENERAL,
            )
            for keyword in keywords
        ]
    )


@transaction.atomic
def create_requirement(*, form, actor) -> IntelligenceRequirement:
    requirement = form.save(commit=False)
    requirement.code = _next_requirement_code()
    requirement.status = IntelligenceRequirement.Status.DRAFT
    requirement.is_active = False
    requirement.created_by = actor
    requirement.save()
    _sync_targets(
        requirement,
        diseases=list(form.cleaned_data["diseases"]),
        locations=list(form.cleaned_data["locations"]),
        keywords=form.keywords(),
    )
    _history(
        requirement,
        action=RequirementHistory.Action.CREATED,
        actor=actor,
        notes="Draf kebutuhan intelijen dibentuk.",
    )
    return requirement


@transaction.atomic
def update_requirement(*, requirement, form, actor) -> IntelligenceRequirement:
    requirement = IntelligenceRequirement.objects.select_for_update().get(
        pk=requirement.pk
    )
    if not requirement.is_editable:
        raise ValidationError(
            "Substansi kebutuhan yang telah terjawab atau ditutup tidak dapat diubah."
        )
    updated = form.save(commit=False)
    for field in (
        "title",
        "question",
        "description",
        "requirement_type",
        "priority",
        "valid_from",
        "valid_until",
        "assigned_to",
    ):
        setattr(requirement, field, getattr(updated, field))
    requirement.save()
    _sync_targets(
        requirement,
        diseases=list(form.cleaned_data["diseases"]),
        locations=list(form.cleaned_data["locations"]),
        keywords=form.keywords(),
    )
    _history(
        requirement,
        action=RequirementHistory.Action.UPDATED,
        actor=actor,
        notes="Substansi dan sasaran kebutuhan diperbarui.",
    )
    return requirement


def activation_blockers(requirement) -> list[str]:
    blockers = []
    if not requirement.question.strip():
        blockers.append("Pertanyaan utama belum diisi.")
    if not requirement.requirement_diseases.exists():
        blockers.append("Minimal satu penyakit sasaran wajib dipilih.")
    if not requirement.requirement_locations.exists():
        blockers.append("Minimal satu wilayah sasaran wajib dipilih.")
    if not requirement.assigned_to_id:
        blockers.append("Penanggung jawab belum ditetapkan.")
    if requirement.valid_until and requirement.valid_until < timezone.localdate():
        blockers.append("Masa berlaku kebutuhan sudah berakhir.")
    return blockers


def _match_existing_indicators(requirement) -> int:
    matched = 0
    indicators = Indicator.objects.filter(
        status__in={Indicator.Status.VALIDATED, Indicator.Status.CORRECTED}
    ).select_related("indicator_type", "disease", "location")
    for indicator in indicators.iterator():
        eligible, _ = indicator_is_eligible_for_signal(indicator)
        reference_date = indicator.event_date or timezone.localdate()
        if not eligible or not requirement_is_active_on_date(
            requirement, reference_date
        ):
            continue
        candidate = calculate_requirement_match(
            indicator=indicator,
            requirement=requirement,
        )
        if not candidate:
            continue
        RequirementIndicator.objects.update_or_create(
            requirement=requirement,
            indicator=indicator,
            defaults={
                "relevance_score": candidate.relevance_score,
                "relevance_reason": " ".join(candidate.reasons),
                "matched_by": RequirementIndicator.MatchSource.SYSTEM,
            },
        )
        matched += 1
    return matched


@transaction.atomic
def activate_requirement(*, requirement, actor, notes):
    requirement = IntelligenceRequirement.objects.select_for_update().get(
        pk=requirement.pk
    )
    if requirement.status != IntelligenceRequirement.Status.DRAFT:
        raise ValidationError("Hanya kebutuhan berstatus Draf yang dapat diaktifkan.")
    blockers = activation_blockers(requirement)
    if blockers:
        raise ValidationError(blockers)
    previous = requirement.status
    requirement.status = IntelligenceRequirement.Status.ACTIVE
    requirement.is_active = True
    requirement.activated_by = actor
    requirement.activated_at = timezone.now()
    requirement.save(
        update_fields=(
            "status",
            "is_active",
            "activated_by",
            "activated_at",
            "updated_at",
        )
    )
    matched_indicators = _match_existing_indicators(requirement)
    _history(
        requirement,
        action=RequirementHistory.Action.ACTIVATED,
        actor=actor,
        from_status=previous,
        notes=notes,
        metadata={"matched_existing_indicators": matched_indicators},
    )
    return requirement


def answer_eligibility(requirement) -> RequirementAnswerEligibility:
    blockers = []
    active_signals = Signal.objects.filter(
        signal_requirements__requirement=requirement,
        status__in=ACTIVE_SIGNAL_STATUSES,
    ).distinct()
    if not active_signals.exists():
        blockers.append("Belum ada sinyal tervalidasi yang menjawab kebutuhan.")
    if not SignalAssessment.objects.filter(
        signal__in=active_signals,
        is_current=True,
        status=SignalAssessment.Status.COMPLETED,
    ).exists():
        blockers.append("Belum ada assessment aktif yang selesai.")
    if requirement.information_gaps.filter(
        status=RequirementInformationGap.Status.OPEN,
        priority=RequirementInformationGap.Priority.HIGH,
    ).exists():
        blockers.append("Kesenjangan informasi prioritas tinggi masih terbuka.")
    return RequirementAnswerEligibility(
        is_eligible=not blockers,
        blockers=tuple(blockers),
    )


@transaction.atomic
def answer_requirement(*, requirement, actor, answer_summary, notes):
    requirement = IntelligenceRequirement.objects.select_for_update().get(
        pk=requirement.pk
    )
    if requirement.status != IntelligenceRequirement.Status.ACTIVE:
        raise ValidationError("Hanya kebutuhan Aktif yang dapat dinyatakan terjawab.")
    eligibility = answer_eligibility(requirement)
    if not eligibility.is_eligible:
        raise ValidationError(list(eligibility.blockers))
    previous = requirement.status
    requirement.status = IntelligenceRequirement.Status.ANSWERED
    requirement.is_active = False
    requirement.answer_summary = answer_summary.strip()
    requirement.answered_by = actor
    requirement.answered_at = timezone.now()
    requirement.save(
        update_fields=(
            "status",
            "is_active",
            "answer_summary",
            "answered_by",
            "answered_at",
            "updated_at",
        )
    )
    _history(
        requirement,
        action=RequirementHistory.Action.ANSWERED,
        actor=actor,
        from_status=previous,
        notes=notes,
    )
    return requirement


@transaction.atomic
def close_requirement(*, requirement, actor, notes):
    requirement = IntelligenceRequirement.objects.select_for_update().get(
        pk=requirement.pk
    )
    if requirement.status != IntelligenceRequirement.Status.ANSWERED:
        raise ValidationError("Hanya kebutuhan Terjawab yang dapat ditutup.")
    previous = requirement.status
    requirement.status = IntelligenceRequirement.Status.CLOSED
    requirement.is_active = False
    requirement.closure_notes = notes.strip()
    requirement.closed_by = actor
    requirement.closed_at = timezone.now()
    requirement.save(
        update_fields=(
            "status",
            "is_active",
            "closure_notes",
            "closed_by",
            "closed_at",
            "updated_at",
        )
    )
    _history(
        requirement,
        action=RequirementHistory.Action.CLOSED,
        actor=actor,
        from_status=previous,
        notes=notes,
    )
    return requirement


@transaction.atomic
def link_collection_session(*, requirement, session, actor, notes=""):
    _ensure_requirement_editable(requirement)
    link, created = RequirementCollectionSession.objects.get_or_create(
        requirement=requirement,
        session=session,
        defaults={"linked_by": actor, "notes": notes},
    )
    if created:
        _history(
            requirement,
            action=RequirementHistory.Action.COLLECTION_LINKED,
            actor=actor,
            notes=notes or f"Sesi {session.reference} ditautkan.",
            metadata={"session_id": str(session.pk), "reference": session.reference},
        )
    return link


def _validated_article_queryset(queryset):
    return queryset.filter(
        validation_assessment__validation_status=(
            ArticleValidationAssessment.ValidationStatus.VALIDATED
        )
    )


@transaction.atomic
def link_article(*, requirement, article, actor, reason, link_source=None):
    _ensure_requirement_editable(requirement)
    if not _validated_article_queryset(Article.objects.filter(pk=article.pk)).exists():
        raise ValidationError("Hanya artikel tervalidasi yang dapat ditautkan.")
    link, created = RequirementArticle.objects.get_or_create(
        requirement=requirement,
        article=article,
        defaults={
            "linked_by": actor,
            "relevance_reason": reason,
            "link_source": link_source or RequirementArticle.LinkSource.ANALYST,
        },
    )
    if created:
        _history(
            requirement,
            action=RequirementHistory.Action.ARTICLE_LINKED,
            actor=actor,
            notes=reason,
            metadata={"article_id": str(article.pk), "title": article.title},
        )
    return link


@transaction.atomic
def unlink_article(*, requirement, article, actor):
    _ensure_requirement_editable(requirement)
    deleted, _ = RequirementArticle.objects.filter(
        requirement=requirement,
        article=article,
    ).delete()
    if deleted:
        _history(
            requirement,
            action=RequirementHistory.Action.ARTICLE_UNLINKED,
            actor=actor,
            notes=f"Tautan artikel dilepas: {article.title}",
            metadata={"article_id": str(article.pk)},
        )


@transaction.atomic
def sync_collection_articles(*, requirement, actor) -> int:
    _ensure_requirement_editable(requirement)
    articles = _validated_article_queryset(
        Article.objects.filter(
            collection_items__collection_job__session__requirement_links__requirement=(
                requirement
            )
        ).distinct()
    )
    created_count = 0
    for article in articles:
        _, created = RequirementArticle.objects.get_or_create(
            requirement=requirement,
            article=article,
            defaults={
                "linked_by": actor,
                "relevance_reason": "Dihasilkan dari sesi koleksi terarah.",
                "link_source": RequirementArticle.LinkSource.COLLECTION,
            },
        )
        created_count += int(created)
    if created_count:
        _history(
            requirement,
            action=RequirementHistory.Action.ARTICLE_LINKED,
            actor=actor,
            notes=f"{created_count} artikel tervalidasi disinkronkan dari sesi koleksi.",
            metadata={"created_count": created_count},
        )
    return created_count


@transaction.atomic
def open_information_gap(*, requirement, actor, description, priority):
    _ensure_requirement_editable(requirement)
    gap = RequirementInformationGap.objects.create(
        requirement=requirement,
        description=description,
        priority=priority,
        created_by=actor,
    )
    _history(
        requirement,
        action=RequirementHistory.Action.GAP_OPENED,
        actor=actor,
        notes=description,
        metadata={"gap_id": str(gap.pk), "priority": priority},
    )
    return gap


@transaction.atomic
def resolve_information_gap(*, gap, actor, notes):
    gap = RequirementInformationGap.objects.select_for_update().get(pk=gap.pk)
    if gap.requirement.status == IntelligenceRequirement.Status.CLOSED:
        raise ValidationError(
            "Kesenjangan pada kebutuhan yang telah Ditutup tidak dapat diubah."
        )
    if gap.status != RequirementInformationGap.Status.OPEN:
        raise ValidationError("Hanya kesenjangan Terbuka yang dapat dipenuhi.")
    gap.status = RequirementInformationGap.Status.RESOLVED
    gap.resolution_notes = notes.strip()
    gap.resolved_by = actor
    gap.resolved_at = timezone.now()
    gap.save()
    _history(
        gap.requirement,
        action=RequirementHistory.Action.GAP_RESOLVED,
        actor=actor,
        notes=notes,
        metadata={"gap_id": str(gap.pk)},
    )
    return gap


def requirement_evidence_articles(requirement):
    direct_ids = requirement.article_links.values_list("article_id", flat=True)
    session_ids = Article.objects.filter(
        collection_items__collection_job__session__requirement_links__requirement=(
            requirement
        )
    ).values_list("pk", flat=True)
    indicator_ids = Article.objects.filter(
        indicator_evidences__indicator__requirement_matches__requirement=(
            requirement
        )
    ).values_list("pk", flat=True)
    signal_ids = Article.objects.filter(
        signal_links__signal__signal_requirements__requirement=requirement
    ).values_list("pk", flat=True)
    article_ids = set(direct_ids) | set(session_ids) | set(indicator_ids) | set(signal_ids)
    return _validated_article_queryset(
        Article.objects.filter(pk__in=article_ids).select_related("source")
    ).order_by("-published_at", "-crawled_at")


def build_requirement_coverage(requirement) -> RequirementCoverage:
    signals = Signal.objects.filter(
        signal_requirements__requirement=requirement,
        status__in=ACTIVE_SIGNAL_STATUSES,
    ).distinct()
    assessments = SignalAssessment.objects.filter(
        signal__in=signals,
        is_current=True,
        status=SignalAssessment.Status.COMPLETED,
    )
    warnings = EarlyWarning.objects.filter(
        signal__in=signals,
        is_current=True,
        status=EarlyWarning.Status.ISSUED,
    )
    recommendations = IntelligenceRecommendation.objects.filter(
        signal__in=signals,
        is_current=True,
    ).exclude(
        status__in={
            IntelligenceRecommendation.Status.CANCELED,
            IntelligenceRecommendation.Status.SUPERSEDED,
        }
    )
    reports = IntelligenceReportSection.objects.filter(
        signal__in=signals
    ).values("report_id").distinct()
    coverage = RequirementCoverage(
        collection_sessions=requirement.collection_links.count(),
        articles=requirement_evidence_articles(requirement).count(),
        indicators=requirement.indicator_matches.count(),
        signals=signals.count(),
        assessments=assessments.count(),
        warnings=warnings.count(),
        recommendations=recommendations.count(),
        reports=reports.count(),
        open_gaps=requirement.information_gaps.filter(
            status=RequirementInformationGap.Status.OPEN
        ).count(),
    )
    if not coverage.collection_sessions:
        coverage.system_gaps.append("Belum ada sesi koleksi yang diarahkan pada kebutuhan ini.")
    if not coverage.articles:
        coverage.system_gaps.append("Belum ada artikel tervalidasi sebagai bukti.")
    if not coverage.indicators:
        coverage.system_gaps.append("Belum ada indikator yang cocok dengan sasaran kebutuhan.")
    if not coverage.signals:
        coverage.system_gaps.append("Belum ada sinyal tervalidasi yang menjawab kebutuhan.")
    if coverage.signals and not coverage.assessments:
        coverage.system_gaps.append("Sinyal tersedia, tetapi assessment aktif belum selesai.")
    return coverage
