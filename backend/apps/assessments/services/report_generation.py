"""Template draft laporan intelijen dari satu sinyal tervalidasi.

Modul ini tidak memakai LLM. Semua kalimat awal diturunkan dari bukti yang
sudah terhubung ke sinyal, assessment aktif, peringatan dini, dan rekomendasi
yang sudah ditetapkan. Draft tetap wajib ditinjau analis.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apps.assessments.models import (
    EarlyWarning,
    IntelligenceRecommendation,
    SignalAssessment,
)
from apps.entities.models import ArticleFact, ValidationStatus
from apps.signals.models import Signal, SignalArticle


@dataclass
class GeneratedSectionDraft:
    indikasi_text: str = ""
    analisis_text: str = ""
    dampak_text: str = ""
    upaya_text: str = ""
    saran_tindak_text: str = ""
    source_article_ids: list = field(default_factory=list)
    assessment_version: int | None = None
    warning_version: int | None = None
    recommendation_version: int | None = None


def _format_count_phrase(fact: ArticleFact | None) -> str:
    if fact is None:
        return ""

    parts = []
    if fact.case_count is not None:
        parts.append(f"{fact.case_count:,} kasus".replace(",", "."))
    if fact.death_count is not None:
        parts.append(f"{fact.death_count:,} kematian".replace(",", "."))
    if fact.hospitalized_count is not None:
        parts.append(
            f"{fact.hospitalized_count:,} orang dirawat".replace(",", ".")
        )
    if fact.recovery_count is not None:
        parts.append(f"{fact.recovery_count:,} sembuh".replace(",", "."))
    return ", ".join(parts)


def _validated_fact(article, signal: Signal) -> ArticleFact | None:
    return (
        article.facts.filter(
            validation_status__in=[
                ValidationStatus.VALIDATED,
                ValidationStatus.CORRECTED,
            ],
            disease=signal.primary_disease,
        )
        .order_by("-confidence_score", "-event_date", "-created_at")
        .first()
    )


def _article_indication(article, signal: Signal) -> str:
    fact = _validated_fact(article, signal)
    source_name = article.source.name
    location_name = signal.primary_location.name
    disease_name = signal.primary_disease.name

    lead = (
        f'Dalam situs {source_name} terdapat artikel berjudul '
        f'“{article.title}”.'
    )

    details = []
    count_phrase = _format_count_phrase(fact)
    if count_phrase:
        details.append(count_phrase)
    if fact and fact.event_date:
        details.append(f"pada {fact.event_date.strftime('%d-%m-%Y')}")

    evidence = (
        fact.fact_text.strip()
        if fact and fact.fact_text.strip()
        else article.excerpt.strip()
    )

    attribution = article.author.strip() or source_name
    statement = (
        f"{attribution} melaporkan perkembangan {disease_name} "
        f"di {location_name}"
    )
    if details:
        statement += f" dengan informasi {', '.join(details)}"
    statement += "."

    if evidence:
        statement += f" Informasi pendukung menyebutkan {evidence.rstrip('.')}."

    return f"{lead} {statement}"


def _signal_articles(signal: Signal) -> list:
    links = (
        signal.signal_articles.select_related("article__source")
        .prefetch_related("article__facts")
        .exclude(support_type=SignalArticle.SupportType.CONTRADICTING)
        .filter(
            article__validation_assessment__validation_status="validated",
        )
        .order_by("-is_primary_source", "-relevance_score", "-added_at")
    )
    return [link.article for link in links[:6]]


def _current_assessment(signal: Signal) -> SignalAssessment | None:
    return (
        signal.assessments.filter(
            is_current=True,
            status=SignalAssessment.Status.COMPLETED,
        )
        .order_by("-version")
        .first()
    )


def _current_warning(signal: Signal) -> EarlyWarning | None:
    return (
        signal.early_warnings.filter(
            is_current=True,
            status=EarlyWarning.Status.ISSUED,
        )
        .order_by("-version")
        .first()
    )


def _current_recommendation(signal: Signal) -> IntelligenceRecommendation | None:
    return (
        signal.intelligence_recommendations.filter(
            is_current=True,
            status__in=[
                IntelligenceRecommendation.Status.APPROVED,
                IntelligenceRecommendation.Status.IN_PROGRESS,
                IntelligenceRecommendation.Status.COMPLETED,
            ],
        )
        .order_by("-version")
        .first()
    )


def generate_signal_section_draft(*, signal: Signal) -> GeneratedSectionDraft:
    """Bangkitkan satu poin laporan hanya dari evidence milik sinyal."""
    articles = _signal_articles(signal)
    assessment = _current_assessment(signal)
    warning = _current_warning(signal)
    recommendation = _current_recommendation(signal)

    indikasi = " ".join(_article_indication(article, signal) for article in articles)

    judgement = ""
    implications = ""
    if warning is not None:
        judgement = warning.analytical_judgement.strip()
        implications = warning.implications.strip()
    elif assessment is not None:
        judgement = assessment.analytical_judgement.strip()
        implications = assessment.implications.strip()

    if not judgement:
        judgement = (
            f"Perkembangan {signal.primary_disease.name} di "
            f"{signal.primary_location.name} masih memerlukan pendalaman "
            "dan verifikasi lanjutan."
        )

    analysis_parts = [judgement]
    if implications:
        analysis_parts.append(implications)
    analysis_parts.append(
        "Perubahan jumlah kasus, perluasan wilayah, atau munculnya bukti "
        "penguat dari sumber independen perlu dijadikan pemicu pembaruan "
        "assessment dan kewaspadaan dini."
    )

    dampak = implications or (
        f"Apabila perkembangan {signal.primary_disease.name} tidak terpantau, "
        "situasi berpotensi mengurangi kecepatan verifikasi dan kesiapan "
        "respons pada wilayah terdampak."
    )

    upaya = (
        "Tim analis MedIntel terus melakukan monitoring terhadap perkembangan "
        f"{signal.primary_disease.name} di {signal.primary_location.name}, "
        "memutakhirkan bukti dari media online berbasis artikel/web dan situs "
        "resmi, serta mencatat kesenjangan informasi yang masih memerlukan "
        "konfirmasi."
    )

    if recommendation is not None:
        target = recommendation.target_unit.strip() or "instansi terkait"
        action = recommendation.recommended_action.strip()
        saran = f"{target} perlu {action[0].lower()}{action[1:]}" if action else ""
    else:
        saran = (
            "Instansi terkait perlu melakukan verifikasi data kasus dan "
            "perkembangan wilayah terdampak sebelum menetapkan tindak lanjut "
            "operasional."
        )

    return GeneratedSectionDraft(
        indikasi_text=indikasi,
        analisis_text=" ".join(analysis_parts),
        dampak_text=dampak,
        upaya_text=upaya,
        saran_tindak_text=saran,
        source_article_ids=[article.id for article in articles],
        assessment_version=assessment.version if assessment else None,
        warning_version=warning.version if warning else None,
        recommendation_version=(recommendation.version if recommendation else None),
    )


def generate_section_draft(*, disease=None, signal: Signal | None = None):
    """Kompatibilitas pemanggil lama dengan pemilihan sinyal yang aman."""
    if signal is None and disease is not None:
        signal = (
            Signal.objects.filter(
                primary_disease=disease,
                status__in=[
                    Signal.Status.VALIDATED,
                    Signal.Status.CORRECTED,
                    Signal.Status.ESCALATED,
                ],
            )
            .order_by("-last_updated_at")
            .first()
        )
    if signal is None:
        return GeneratedSectionDraft()
    return generate_signal_section_draft(signal=signal)
