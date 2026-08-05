from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction

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

from ..models import Signal, SignalArticle, SignalHistory
from .generation import generate_signal_from_indicator


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
            return "Fakta numerik belum tersedia"

        parts = []
        if self.fact.case_count is not None:
            parts.append(f"{self.fact.case_count:,} kasus".replace(",", "."))
        if self.fact.death_count is not None:
            parts.append(
                f"{self.fact.death_count:,} kematian".replace(",", ".")
            )
        return ", ".join(parts) or "Fakta numerik belum tersedia"

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
    if fact is None:
        blockers.append(
            "Fakta numerik tervalidasi belum sesuai dengan penyakit utama."
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
            + (" ".join(dict.fromkeys(skipped_notes)) or "Tidak ada indikator siap pakai.")
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
            "indicators_used": indicators_used,
            "requirements_matched": requirements_matched,
        },
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
