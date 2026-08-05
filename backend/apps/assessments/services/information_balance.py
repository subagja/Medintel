import re
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Q

from apps.articles.models import Article
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    ValidationStatus,
)
from apps.sources.models import Source

from ..models import ArticleValidationAssessment


RECOMMENDATION_RULE_VERSION = "admiralty-rule-v1"
MIN_SOURCE_HISTORY = 5
CORROBORATION_WINDOW_DAYS = 30

OFFICIAL_ATTRIBUTION_PATTERN = re.compile(
    r"\b(?:"
    r"kementerian\s+kesehatan|kemenkes|"
    r"dinas\s+kesehatan|dinkes|"
    r"puskesmas|rumah\s+sakit|rsud|"
    r"world\s+health\s+organization|who|"
    r"badan\s+kesehatan\s+dunia|"
    r"laboratorium|balai\s+besar\s+laboratorium"
    r")\b",
    flags=re.IGNORECASE,
)

SOURCE_RATING_VALUE = {
    ArticleValidationAssessment.SourceReliability.A: 5,
    ArticleValidationAssessment.SourceReliability.B: 4,
    ArticleValidationAssessment.SourceReliability.C: 3,
    ArticleValidationAssessment.SourceReliability.D: 2,
    ArticleValidationAssessment.SourceReliability.E: 1,
}


@dataclass(frozen=True)
class InformationBalanceRecommendation:
    source_reliability: str
    information_credibility: int
    source_reason: str
    information_reason: str
    supporting_factors: tuple[str, ...]
    limiting_factors: tuple[str, ...]
    source_history_count: int
    source_history_average: float | None
    corroborating_sources: tuple[str, ...]
    rule_version: str = RECOMMENDATION_RULE_VERSION

    @property
    def admiralty_code(self) -> str:
        return (
            f"{self.source_reliability}"
            f"{self.information_credibility}"
        )

    @property
    def source_label(self) -> str:
        return dict(
            ArticleValidationAssessment.SourceReliability.choices
        )[self.source_reliability]

    @property
    def information_label(self) -> str:
        return dict(
            ArticleValidationAssessment
            .InformationCredibility
            .choices
        )[self.information_credibility]

    @property
    def suggested_notes(self) -> str:
        notes = (
            f"Rekomendasi sistem {self.admiralty_code}. "
            f"{self.source_reason} {self.information_reason}"
        )

        if self.limiting_factors:
            notes += (
                " Keterbatasan: "
                + "; ".join(self.limiting_factors)
                + "."
            )

        return notes


@dataclass(frozen=True)
class _ArticleEvidence:
    disease_ids: frozenset
    primary_location_ids: frozenset
    numeric_facts: tuple[ArticleFact, ...]
    has_supported_numeric_fact: bool
    structured_evidence_complete: bool
    official_attribution: bool


def recommend_information_balance(
    article: Article,
) -> InformationBalanceRecommendation:
    (
        source_reliability,
        source_reason,
        source_support,
        source_limits,
        history_count,
        history_average,
    ) = _recommend_source_reliability(article)

    evidence = _collect_article_evidence(article)
    corroborating_sources = _find_corroborating_sources(
        article=article,
        evidence=evidence,
    )

    (
        information_credibility,
        information_reason,
        information_support,
        information_limits,
    ) = _recommend_information_credibility(
        evidence=evidence,
        corroborating_sources=corroborating_sources,
    )

    return InformationBalanceRecommendation(
        source_reliability=source_reliability,
        information_credibility=information_credibility,
        source_reason=source_reason,
        information_reason=information_reason,
        supporting_factors=tuple(
            source_support + information_support
        ),
        limiting_factors=tuple(
            source_limits + information_limits
        ),
        source_history_count=history_count,
        source_history_average=history_average,
        corroborating_sources=tuple(corroborating_sources),
    )


def _recommend_source_reliability(article: Article):
    history = list(
        ArticleValidationAssessment.objects.filter(
            article__source=article.source,
            evaluated_by__isnull=False,
        )
        .exclude(article=article)
        .exclude(
            source_reliability=(
                ArticleValidationAssessment
                .SourceReliability
                .F
            )
        )
        .values_list("source_reliability", flat=True)
    )

    history_values = [
        SOURCE_RATING_VALUE[rating]
        for rating in history
        if rating in SOURCE_RATING_VALUE
    ]
    history_count = len(history_values)
    history_average = (
        sum(history_values) / history_count
        if history_count
        else None
    )

    supporting = []
    limiting = []

    if history_count >= MIN_SOURCE_HISTORY:
        rating = _source_rating_from_average(history_average)
        reason = (
            f"Rekam penilaian analis terhadap {article.source.name} "
            f"tersedia pada {history_count} artikel dengan rerata "
            f"{history_average:.2f} dari 5."
        )
        supporting.append(
            f"Rekam penilaian analis tersedia pada {history_count} artikel."
        )
        return (
            rating,
            reason,
            supporting,
            limiting,
            history_count,
            history_average,
        )

    if article.source.is_verified:
        rating = ArticleValidationAssessment.SourceReliability.B
        reason = (
            f"Profil {article.source.name} telah diverifikasi dalam "
            "master sumber dan dinilai biasanya dapat dipercaya."
        )
        supporting.append("Profil sumber telah diverifikasi.")

        if history_count:
            limiting.append(
                f"Rekam penilaian baru {history_count} artikel; "
                f"minimum aturan adalah {MIN_SOURCE_HISTORY}"
            )
        else:
            limiting.append(
                "Belum ada rekam penilaian analis sebelumnya"
            )

        return (
            rating,
            reason,
            supporting,
            limiting,
            history_count,
            history_average,
        )

    rating = ArticleValidationAssessment.SourceReliability.F
    reason = (
        f"Reliabilitas {article.source.name} belum dapat dinilai "
        "karena profil belum diverifikasi dan rekam penilaian belum "
        "mencukupi."
    )
    limiting.extend(
        [
            "Profil sumber belum diverifikasi",
            (
                f"Rekam penilaian kurang dari {MIN_SOURCE_HISTORY} "
                "artikel"
            ),
        ]
    )
    return (
        rating,
        reason,
        supporting,
        limiting,
        history_count,
        history_average,
    )


def _source_rating_from_average(
    average: float,
) -> str:
    if average >= 4.8:
        return ArticleValidationAssessment.SourceReliability.A
    if average >= 3.8:
        return ArticleValidationAssessment.SourceReliability.B
    if average >= 2.8:
        return ArticleValidationAssessment.SourceReliability.C
    if average >= 1.8:
        return ArticleValidationAssessment.SourceReliability.D
    return ArticleValidationAssessment.SourceReliability.E


def _collect_article_evidence(
    article: Article,
) -> _ArticleEvidence:
    disease_ids = frozenset(
        ArticleDisease.objects.filter(article=article)
        .exclude(validation_status=ValidationStatus.REJECTED)
        .values_list("disease_id", flat=True)
    )
    primary_location_ids = frozenset(
        ArticleLocation.objects.filter(
            article=article,
            is_primary=True,
        )
        .exclude(validation_status=ValidationStatus.REJECTED)
        .values_list("location_id", flat=True)
    )
    numeric_facts = tuple(
        ArticleFact.objects.filter(article=article)
        .exclude(validation_status=ValidationStatus.REJECTED)
        .filter(
            Q(case_count__isnull=False)
            | Q(death_count__isnull=False)
            | Q(recovery_count__isnull=False)
            | Q(hospitalized_count__isnull=False)
        )
    )

    matching_facts = [
        fact
        for fact in numeric_facts
        if fact.disease_id in disease_ids
        and fact.location_id in primary_location_ids
    ]
    has_supported_numeric_fact = any(
        bool(fact.fact_text.strip())
        for fact in numeric_facts
    )
    structured_evidence_complete = bool(
        disease_ids
        and len(primary_location_ids) == 1
        and matching_facts
        and any(
            bool(fact.fact_text.strip())
            for fact in matching_facts
        )
    )

    article_text = " ".join(
        [
            article.title or "",
            article.excerpt or "",
            article.content_text or "",
        ]
    )
    official_source_types = {
        Source.SourceType.GOVERNMENT,
        Source.SourceType.HEALTH_ORGANIZATION,
        Source.SourceType.RESEARCH_INSTITUTION,
    }
    official_attribution = bool(
        OFFICIAL_ATTRIBUTION_PATTERN.search(article_text)
        or (
            article.source.is_verified
            and article.source.source_type
            in official_source_types
        )
    )

    return _ArticleEvidence(
        disease_ids=disease_ids,
        primary_location_ids=primary_location_ids,
        numeric_facts=numeric_facts,
        has_supported_numeric_fact=has_supported_numeric_fact,
        structured_evidence_complete=structured_evidence_complete,
        official_attribution=official_attribution,
    )


def _find_corroborating_sources(
    *,
    article: Article,
    evidence: _ArticleEvidence,
) -> list[str]:
    if (
        not evidence.disease_ids
        or len(evidence.primary_location_ids) != 1
        or not any(
            fact.disease_id in evidence.disease_ids
            and fact.location_id
            in evidence.primary_location_ids
            for fact in evidence.numeric_facts
        )
    ):
        return []

    candidates = (
        Article.objects.filter(
            article_diseases__disease_id__in=(
                evidence.disease_ids
            ),
            article_locations__location_id__in=(
                evidence.primary_location_ids
            ),
            article_locations__is_primary=True,
            validation_assessment__validation_status=(
                ArticleValidationAssessment
                .ValidationStatus
                .VALIDATED
            ),
            source__is_verified=True,
        )
        .exclude(pk=article.pk)
        .exclude(source=article.source)
        .exclude(
            processing_status__in=[
                Article.ProcessingStatus.REJECTED,
                Article.ProcessingStatus.FAILED,
            ]
        )
        .select_related("source")
        .prefetch_related("facts")
        .distinct()
    )

    if article.published_at is not None:
        window = timedelta(days=CORROBORATION_WINDOW_DAYS)
        candidates = candidates.filter(
            published_at__gte=article.published_at - window,
            published_at__lte=article.published_at + window,
        )

    source_names = set()

    primary_numeric_facts = [
        fact
        for fact in evidence.numeric_facts
        if fact.disease_id in evidence.disease_ids
        and fact.location_id in evidence.primary_location_ids
    ]

    for candidate in candidates[:50]:
        candidate_facts = [
            fact
            for fact in candidate.facts.all()
            if fact.validation_status
            != ValidationStatus.REJECTED
            and fact.disease_id in evidence.disease_ids
            and fact.location_id
            in evidence.primary_location_ids
        ]

        if any(
            _facts_numerically_align(primary, corroborating)
            for primary in primary_numeric_facts
            for corroborating in candidate_facts
        ):
            source_names.add(candidate.source.name)

    return sorted(source_names)


def _facts_numerically_align(
    first: ArticleFact,
    second: ArticleFact,
) -> bool:
    for field_name in (
        "case_count",
        "death_count",
        "recovery_count",
        "hospitalized_count",
    ):
        first_value = getattr(first, field_name)
        second_value = getattr(second, field_name)

        if first_value is None or second_value is None:
            continue

        tolerance = max(1, round(first_value * 0.10))

        if abs(first_value - second_value) <= tolerance:
            return True

    return False


def _recommend_information_credibility(
    *,
    evidence: _ArticleEvidence,
    corroborating_sources: list[str],
):
    supporting = []
    limiting = []

    if evidence.structured_evidence_complete:
        supporting.append(
            "Penyakit, satu lokasi utama, angka, dan kutipan bukti tersedia."
        )
    else:
        if not evidence.disease_ids:
            limiting.append("Penyakit belum terstruktur")
        if len(evidence.primary_location_ids) != 1:
            limiting.append(
                "Belum ada tepat satu lokasi kejadian utama"
            )
        if not evidence.numeric_facts:
            limiting.append("Fakta numerik belum tersedia")
        elif not evidence.has_supported_numeric_fact:
            limiting.append(
                "Fakta numerik belum memiliki kutipan bukti"
            )

    if evidence.official_attribution:
        supporting.append(
            "Artikel memuat atribusi instansi kesehatan atau berasal dari sumber resmi."
        )

    if corroborating_sources:
        supporting.append(
            "Angka selaras dengan artikel tervalidasi dari sumber berbeda: "
            + ", ".join(corroborating_sources)
            + "."
        )
    else:
        limiting.append(
            "Belum ditemukan penguatan numerik dari sumber berbeda yang tervalidasi"
        )

    if (
        evidence.structured_evidence_complete
        and len(corroborating_sources) >= 2
    ):
        return (
            ArticleValidationAssessment
            .InformationCredibility
            .CONFIRMED,
            (
                "Informasi dikonfirmasi oleh sedikitnya dua sumber "
                "berbeda yang telah tervalidasi dengan angka selaras."
            ),
            supporting,
            limiting,
        )

    if (
        evidence.structured_evidence_complete
        and (
            evidence.official_attribution
            or corroborating_sources
        )
    ):
        return (
            ArticleValidationAssessment
            .InformationCredibility
            .PROBABLY_TRUE,
            (
                "Informasi kemungkinan besar benar karena unsur inti "
                "lengkap dan didukung atribusi resmi atau sumber berbeda."
            ),
            supporting,
            limiting,
        )

    if evidence.structured_evidence_complete:
        return (
            ArticleValidationAssessment
            .InformationCredibility
            .POSSIBLY_TRUE,
            (
                "Informasi mungkin benar; unsur inti lengkap, tetapi "
                "konfirmasi eksternal belum memadai."
            ),
            supporting,
            limiting,
        )

    return (
        ArticleValidationAssessment
        .InformationCredibility
        .UNASSESSABLE,
        (
            "Kredibilitas belum dapat dinilai otomatis karena bukti "
            "terstruktur belum lengkap."
        ),
        supporting,
        limiting,
    )
