from dataclasses import dataclass, field
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.articles.models import Article
from apps.assessments.models import ArticleValidationAssessment
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    ValidationStatus,
)
from apps.indicators.models import Indicator, IndicatorEvidence
from apps.indicators.services import (
    generate_indicators_from_fact,
    validate_indicator,
)
from apps.requirements.services import match_indicator_to_requirements
from apps.requirements.models import IntelligenceRequirement

from ..models import (
    Signal,
    SignalArticle,
    SignalDisease,
    SignalHistory,
    SignalLocation,
    SignalRequirement,
)
from .generation import generate_signal_code, generate_signal_from_indicator


REVIEWED_ENTITY_STATUSES = {
    ValidationStatus.VALIDATED,
    ValidationStatus.CORRECTED,
}


@dataclass(frozen=True)
class ArticleSignalCandidate:
    article: Article
    assessment: ArticleValidationAssessment | None
    primary_disease_relation: ArticleDisease | None
    primary_location_relation: ArticleLocation | None
    fact: ArticleFact | None
    is_ready: bool
    blockers: tuple[str, ...] = ()

    @property
    def primary_disease(self):
        if self.primary_disease_relation is None:
            return None
        return self.primary_disease_relation.disease

    @property
    def primary_location(self):
        if self.primary_location_relation is None:
            return None
        return self.primary_location_relation.location

    @property
    def admiralty_code(self) -> str:
        if self.assessment is None:
            return "-"
        return self.assessment.admiralty_code

    @property
    def numeric_fact_label(self) -> str:
        if self.fact is None:
            return "Bukti kualitatif"

        parts = []
        if self.fact.case_count is not None:
            parts.append(f"{self.fact.case_count:,} kasus".replace(",", "."))
        if self.fact.death_count is not None:
            parts.append(
                f"{self.fact.death_count:,} kematian".replace(",", ".")
            )
        return ", ".join(parts) or "Bukti kualitatif"

    @property
    def evidence_mode(self) -> str:
        if self.fact is not None:
            return Signal.EvidenceMode.QUANTITATIVE
        return Signal.EvidenceMode.QUALITATIVE

    @property
    def evidence_mode_label(self) -> str:
        return Signal.EvidenceMode(self.evidence_mode).label

    @property
    def is_qualitative(self) -> bool:
        return self.evidence_mode == Signal.EvidenceMode.QUALITATIVE

    @property
    def suggested_title(self) -> str:
        if self.primary_disease and self.primary_location:
            return (
                f"Indikasi {self.primary_disease.name} di "
                f"{self.primary_location.name}"
            )
        return self.article.title

    @property
    def suggested_summary(self) -> str:
        if self.primary_disease and self.primary_location and self.fact:
            event_label = (
                self.fact.event_date.strftime("%d-%m-%Y")
                if self.fact.event_date
                else "waktu kejadian perlu dikonfirmasi"
            )
            return (
                f"Artikel {self.article.source.name} melaporkan "
                f"{self.numeric_fact_label} {self.primary_disease.name} "
                f"di {self.primary_location.name}. Waktu: {event_label}. "
                f"Neraca informasi: {self.admiralty_code}."
            )
        return self.article.excerpt or self.article.content_text[:700]

    @property
    def suggested_notes(self) -> str:
        if self.is_qualitative:
            return (
                "Artikel berstatus Valid dengan penyakit dan lokasi utama "
                "tervalidasi. Indikasi kualitatif ini perlu dikonfirmasi "
                f"analis; neraca informasi {self.admiralty_code}."
            )
        return (
            "Artikel berstatus Valid, memenuhi kriteria penyakit-lokasi-"
            f"fakta numerik, dan memiliki neraca informasi {self.admiralty_code}."
        )


@dataclass
class SignalFormationResult:
    article: Article
    signal: Signal
    created: bool
    merged: bool
    indicators_reviewed: int = 0
    requirements_matched: int = 0
    indicators_used: int = 0
    notes: list[str] = field(default_factory=list)


def _single_primary_relation(queryset, label: str):
    relations = list(
        queryset.filter(
            is_primary=True,
            validation_status__in=REVIEWED_ENTITY_STATUSES,
        )[:2]
    )
    if len(relations) != 1:
        return None, (
            f"Harus ada tepat satu {label} utama yang telah divalidasi."
        )
    return relations[0], ""


def evaluate_article_signal_candidate(article: Article) -> ArticleSignalCandidate:
    blockers = []

    try:
        assessment = article.validation_assessment
    except ArticleValidationAssessment.DoesNotExist:
        assessment = None

    if assessment is None:
        blockers.append("Validasi artikel belum tersedia.")
    else:
        if assessment.validation_status != (
            ArticleValidationAssessment.ValidationStatus.VALIDATED
        ):
            blockers.append("Artikel belum berstatus Valid.")
        if assessment.evaluated_by_id is None:
            blockers.append("Neraca informasi belum dikonfirmasi analis.")
        if assessment.source_reliability == (
            ArticleValidationAssessment.SourceReliability.F
        ) or assessment.information_credibility == (
            ArticleValidationAssessment.InformationCredibility.UNASSESSABLE
        ):
            blockers.append("Neraca informasi masih F6 atau belum dapat dinilai.")

    disease_relation, disease_error = _single_primary_relation(
        article.article_diseases.select_related("disease"),
        "penyakit",
    )
    if disease_error:
        blockers.append(disease_error)

    location_relation, location_error = _single_primary_relation(
        article.article_locations.select_related("location"),
        "lokasi kejadian",
    )
    if location_error:
        blockers.append(location_error)

    facts = article.facts.filter(
        validation_status__in=REVIEWED_ENTITY_STATUSES,
    ).filter(
        case_count__isnull=False,
    ) | article.facts.filter(
        validation_status__in=REVIEWED_ENTITY_STATUSES,
        death_count__isnull=False,
    )

    if disease_relation is not None:
        facts = facts.filter(disease_id=disease_relation.disease_id)
    fact = (
        facts.select_related("disease", "location")
        .order_by("-confidence_score", "-event_date", "-created_at")
        .first()
    )
    return ArticleSignalCandidate(
        article=article,
        assessment=assessment,
        primary_disease_relation=disease_relation,
        primary_location_relation=location_relation,
        fact=fact,
        is_ready=not blockers,
        blockers=tuple(blockers),
    )


def _article_indicators(
    article: Article,
    fact: ArticleFact,
    *,
    disease,
    location,
):
    return (
        Indicator.objects.filter(
            evidences__article=article,
            evidences__article_fact=fact,
            disease=disease,
            location=location,
        )
        .distinct()
        .order_by("created_at")
    )


def _article_reference_date(article: Article):
    timestamp = article.published_at or article.crawled_at
    if timestamp is not None:
        return timezone.localdate(timestamp)
    return timezone.localdate()


def _find_existing_qualitative_signal(candidate: ArticleSignalCandidate):
    reference_date = _article_reference_date(candidate.article)
    start_date = reference_date - timedelta(days=14)
    end_date = reference_date + timedelta(days=14)
    return (
        Signal.objects.filter(
            primary_disease=candidate.primary_disease,
            primary_location=candidate.primary_location,
            event_start_date__range=(start_date, end_date),
        )
        .exclude(status__in=[Signal.Status.REJECTED, Signal.Status.CLOSED])
        .order_by("-last_updated_at")
        .first()
    )


def _link_qualitative_requirements(*, signal, candidate) -> int:
    requirements = (
        IntelligenceRequirement.objects.filter(
            Q(articles=candidate.article)
            | Q(diseases=candidate.primary_disease)
            | Q(locations=candidate.primary_location),
            is_active=True,
            status=IntelligenceRequirement.Status.ACTIVE,
        )
        .distinct()
        .order_by("code")
    )
    direct_ids = set(
        candidate.article.intelligence_requirements.filter(
            is_active=True,
            status=IntelligenceRequirement.Status.ACTIVE,
        ).values_list("id", flat=True)
    )
    matched = 0
    for requirement in requirements:
        _, created = SignalRequirement.objects.get_or_create(
            signal=signal,
            requirement=requirement,
            defaults={
                "relevance_score": 0.8 if requirement.id in direct_ids else 0.65,
                "relevance_reason": (
                    "Kesesuaian artikel, penyakit, atau lokasi pada "
                    "pembentukan sinyal kualitatif."
                ),
                "is_primary": not signal.signal_requirements.exists(),
            },
        )
        matched += int(created)
    return matched


def _form_qualitative_signal(*, candidate, analyst, title, summary):
    signal = _find_existing_qualitative_signal(candidate)
    created = signal is None
    reference_date = _article_reference_date(candidate.article)

    if created:
        signal = Signal.objects.create(
            code=generate_signal_code(),
            title=title.strip(),
            summary=summary.strip(),
            primary_disease=candidate.primary_disease,
            primary_location=candidate.primary_location,
            event_start_date=reference_date,
            event_end_date=reference_date,
            status=Signal.Status.NEEDS_REVIEW,
            priority_level=Signal.PriorityLevel.LOW,
            confidence_level=Signal.ConfidenceLevel.UNASSESSED,
            evidence_mode=Signal.EvidenceMode.QUALITATIVE,
            created_by_system=False,
            created_by=analyst,
            assigned_to=analyst,
        )
        SignalDisease.objects.create(
            signal=signal,
            disease=candidate.primary_disease,
            is_primary=True,
        )
        SignalLocation.objects.create(
            signal=signal,
            location=candidate.primary_location,
            is_primary=True,
        )

    SignalArticle.objects.get_or_create(
        signal=signal,
        article=candidate.article,
        defaults={
            "support_type": (
                SignalArticle.SupportType.PRIMARY
                if created
                else SignalArticle.SupportType.CORROBORATING
            ),
            "is_primary_source": created,
            "added_by": analyst,
        },
    )
    requirements_matched = _link_qualitative_requirements(
        signal=signal,
        candidate=candidate,
    )
    return signal, created, requirements_matched


@transaction.atomic
def form_signal_from_article(
    *,
    article: Article,
    analyst,
    title: str,
    summary: str,
    notes: str,
) -> SignalFormationResult:
    if analyst is None or not getattr(analyst, "is_active", False):
        raise ValidationError("Analis aktif wajib ditentukan.")
    if not title.strip() or not summary.strip() or not notes.strip():
        raise ValidationError(
            "Judul, ringkasan, dan dasar pembentukan wajib diisi."
        )

    existing_link = (
        SignalArticle.objects.select_related("signal")
        .filter(article=article)
        .exclude(
            signal__status__in=[Signal.Status.REJECTED, Signal.Status.CLOSED]
        )
        .order_by("-signal__last_updated_at")
        .first()
    )
    if existing_link is not None:
        return SignalFormationResult(
            article=article,
            signal=existing_link.signal,
            created=False,
            merged=True,
            notes=["Artikel sudah terkait dengan sinyal aktif yang sama."],
        )

    candidate = evaluate_article_signal_candidate(article)
    if not candidate.is_ready:
        raise ValidationError(" ".join(candidate.blockers))

    if candidate.is_qualitative:
        signal, created, requirements_matched = _form_qualitative_signal(
            candidate=candidate,
            analyst=analyst,
            title=title,
            summary=summary,
        )
        merged = not created
        indicators_reviewed = 0
        indicators_used = 0
        skipped_notes = []
    else:
        generation = generate_indicators_from_fact(
            candidate.fact,
            disease=candidate.primary_disease,
            location=candidate.primary_location,
        )
        indicators = list(
            _article_indicators(
                article,
                candidate.fact,
                disease=candidate.primary_disease,
                location=candidate.primary_location,
            )
        )
        if not indicators:
            reason = generation.skipped_reason or (
                "Tidak ada indikator yang dapat dibentuk dari fakta artikel."
            )
            raise ValidationError(reason)

        signals = []
        indicators_reviewed = 0
        requirements_matched = 0
        indicators_used = 0
        skipped_notes = []

        for indicator in indicators:
            if indicator.status == Indicator.Status.REJECTED:
                continue
            if indicator.status in {
                Indicator.Status.DETECTED,
                Indicator.Status.NEEDS_REVIEW,
            }:
                validate_indicator(
                    indicator=indicator,
                    reviewer=analyst,
                    notes=notes,
                )
                indicators_reviewed += 1

            matching = match_indicator_to_requirements(indicator)
            requirements_matched += (
                len(matching.matches_created) + len(matching.matches_updated)
            )
            if matching.skipped_reason:
                skipped_notes.append(matching.skipped_reason)
                continue

            generation_result = generate_signal_from_indicator(indicator)
            if generation_result.signal is None:
                if generation_result.skipped_reason:
                    skipped_notes.append(generation_result.skipped_reason)
                continue
            signals.append((generation_result.signal, generation_result.created))
            indicators_used += 1

        if not signals:
            raise ValidationError(
                "Sinyal belum dapat dibentuk. "
                + (
                    " ".join(dict.fromkeys(skipped_notes))
                    or "Tidak ada indikator siap pakai."
                )
            )

        signal, created = signals[0]
        merged = not created

    if created:
        signal.title = title.strip()
        signal.summary = summary.strip()
        signal.created_by = analyst
        signal.assigned_to = analyst
        signal.save(
            update_fields=[
                "title",
                "summary",
                "created_by",
                "assigned_to",
                "updated_at",
                "last_updated_at",
            ]
        )

    SignalHistory.objects.create(
        signal=signal,
        from_status="" if created else signal.status,
        to_status=signal.status,
        changed_by=analyst,
        reason=notes.strip(),
        metadata={
            "action": "formation" if created else "evidence_merged",
            "article_id": str(article.pk),
            "admiralty_code": candidate.admiralty_code,
            "evidence_mode": candidate.evidence_mode,
            "indicators_used": indicators_used,
            "requirements_matched": requirements_matched,
        },
    )

    if created:
        from apps.notifications.services import notify_users
        from apps.notifications.models import Notification
        from django.urls import reverse

        notify_users(
            notification_type=Notification.NotificationType.SIGNAL_CREATED,
            title=f"Sinyal baru: {signal.title}",
            body=(
                f"Sinyal terbentuk dari artikel \"{article.title}\". "
                f"Dasar pembentukan: {notes.strip()[:200]}"
            ),
            link_url=(
                reverse("dashboard:signal-workspace")
                + f"?signal={signal.id}"
            ),
            exclude_user=analyst,
        )

    return SignalFormationResult(
        article=article,
        signal=signal,
        created=created,
        merged=merged,
        indicators_reviewed=indicators_reviewed,
        requirements_matched=requirements_matched,
        indicators_used=indicators_used,
        notes=list(dict.fromkeys(skipped_notes)),
    )
