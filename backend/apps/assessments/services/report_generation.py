"""Generasi draft konten laporan (Indikasi/Analisis/Dampak/Upaya/Saran
Tindak) per topik penyakit -- TEMPLATE-BASED, mengisi kalimat berpola
dari data terstruktur yang sudah ada (artikel, assessment, early
warning, rekomendasi). Ini BUKAN generasi bahasa alami oleh AI/LLM --
sistem ini tidak punya integrasi API LLM. Hasilnya dimaksudkan sebagai
draft kasar yang wajib ditinjau dan disunting analis sebelum laporan
difinalkan.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apps.articles.models import Article
from apps.entities.models import ArticleDisease, ArticleFact
from apps.signals.models import Signal


@dataclass
class GeneratedSectionDraft:
    indikasi_text: str = ""
    analisis_text: str = ""
    dampak_text: str = ""
    upaya_text: str = ""
    saran_tindak_text: str = ""
    source_article_ids: list = field(default_factory=list)


def _format_count_phrase(fact: ArticleFact) -> str:
    parts = []
    if fact.case_count:
        parts.append(f"{fact.case_count} kasus")
    if fact.death_count:
        parts.append(f"{fact.death_count} kematian")
    if not parts:
        return ""
    return " dan ".join(parts)


def _indikasi_sentence_for_article(article: Article) -> str:
    """Susun satu kalimat "siapa - mengatakan/melaporkan - apa" dari
    satu artikel, mengikuti pola atribusi di contoh dokumen (nama/
    jabatan sumber diikuti isi laporan).
    """
    who = (
        f"{article.author} ({article.source.name})"
        if article.author
        else article.source.name
    )

    fact = article.facts.order_by("-confidence_score").first()
    location = article.locations.first()
    disease = article.diseases.first()

    what_parts = []
    if disease:
        what_parts.append(f"terjadi peningkatan kasus {disease.name}")
    if location:
        what_parts.append(f"di {location.name}")
    if fact:
        count_phrase = _format_count_phrase(fact)
        if count_phrase:
            what_parts.append(f"dengan {count_phrase}")
        if fact.event_date:
            what_parts.append(
                f"tercatat sejak {fact.event_date.strftime('%d %B %Y')}"
            )

    what = " ".join(what_parts) if what_parts else (article.title or "")

    return f"{who} melaporkan bahwa {what}.".replace("  ", " ")


def generate_indikasi_text(articles: list) -> tuple[str, list]:
    """Gabungkan beberapa artikel jadi satu paragraf indikasi. Maks 4
    artikel per paragraf supaya tidak berlebihan panjangnya -- kalau
    lebih, analis disarankan pecah jadi beberapa poin terpisah.
    """
    connectors = ["", "Selain itu, ", "Sementara itu, ", "Di sisi lain, "]
    sentences = []
    used_ids = []

    for index, article in enumerate(articles[:4]):
        sentence = _indikasi_sentence_for_article(article)
        connector = connectors[index] if index < len(connectors) else ""
        if connector and sentence:
            sentence = connector + sentence[0].lower() + sentence[1:]
        sentences.append(sentence)
        used_ids.append(article.id)

    return " ".join(s for s in sentences if s), used_ids


def generate_analisis_text(
    *,
    disease_name: str,
    early_warning=None,
    assessment=None,
) -> str:
    """Susun paragraf analisis dengan unsur Judgement, Early Warning,
    Forecasting, dan Problem Solving secara tersirat (tidak dilabeli
    eksplisit di teks, mengikuti gaya dokumen contoh).
    """
    if early_warning is not None:
        judgement = early_warning.analytical_judgement.strip()
        forecasting = early_warning.implications.strip()

        parts = []
        if judgement:
            parts.append(judgement)
        parts.append(
            f"Kondisi tersebut menjadi early warning bagi pemangku "
            f"kepentingan terkait untuk memperkuat kewaspadaan "
            f"terhadap potensi perkembangan {disease_name} lebih lanjut."
        )
        if forecasting:
            parts.append(forecasting)

        return " ".join(parts)

    if assessment is not None:
        return (
            f"Berdasarkan penilaian awal, sinyal terkait {disease_name} "
            f"tercatat dengan skor urgensi {assessment.urgency_score}/5 "
            f"dan skor dampak {assessment.impact_score}/5. Kondisi ini "
            f"masih memerlukan assessment analitis lebih lanjut untuk "
            f"menghasilkan judgement dan proyeksi perkembangan yang "
            f"lebih pasti."
        )

    return (
        f"[Perlu diisi manual -- belum ada assessment atau early "
        f"warning tervalidasi untuk topik {disease_name}.]"
    )


def generate_dampak_text(
    *,
    disease_name: str,
    early_warning=None,
) -> str:
    if early_warning is not None and early_warning.implications.strip():
        return (
            f"Apabila tidak dikendalikan, perkembangan {disease_name} "
            f"berpotensi {early_warning.implications.strip()[0].lower()}"
            f"{early_warning.implications.strip()[1:]}"
        )

    return (
        f"[Perlu diisi manual -- dampak potensial dari perkembangan "
        f"{disease_name} belum terdokumentasi dalam sistem.]"
    )


def generate_upaya_text(
    *,
    disease_name: str,
    early_warning=None,
) -> str:
    base = (
        f"Tim analis terus memonitor perkembangan situasi "
        f"{disease_name} baik di dalam maupun luar negeri."
    )

    if early_warning is not None and early_warning.recommended_actions.strip():
        return f"{base} {early_warning.recommended_actions.strip()}"

    return base


def generate_saran_tindak_text(
    *,
    disease_name: str,
    recommendations,
) -> str:
    recs = list(recommendations[:3])

    if not recs:
        return (
            f"[Perlu diisi manual -- belum ada rekomendasi intelijen "
            f"tervalidasi untuk topik {disease_name}.]"
        )

    sentences = []
    for rec in recs:
        target = rec.target_unit or "instansi terkait"
        action = rec.recommended_action.strip()
        if action:
            sentences.append(
                f"{target} perlu {action[0].lower()}{action[1:]}"
            )

    return " ".join(sentences)


def generate_section_draft(
    *,
    disease,
) -> GeneratedSectionDraft:
    """Bangkitkan draft satu poin laporan (semua 5 bagian) untuk satu
    Disease, menarik data dari artikel + Signal + assessment + early
    warning + rekomendasi terkait yang sudah ada di sistem.
    """
    from apps.assessments.models import IntelligenceRecommendation

    articles = list(
        Article.objects.filter(
            diseases=disease,
        )
        .select_related("source")
        .prefetch_related("facts", "locations", "diseases")
        .order_by("-published_at", "-crawled_at")[:6]
    )

    indikasi_text, used_ids = generate_indikasi_text(articles)

    signal = (
        Signal.objects.filter(
            primary_disease=disease,
        )
        .order_by("-first_detected_at")
        .first()
    )

    early_warning = None
    assessment = None
    if signal is not None:
        assessment = (
            signal.assessments.filter(is_current=True).first()
            if hasattr(signal, "assessments")
            else None
        )
        early_warning = (
            signal.early_warnings.filter(is_current=True).first()
            if hasattr(signal, "early_warnings")
            else None
        )

    recommendations = (
        IntelligenceRecommendation.objects.filter(
            signal__primary_disease=disease,
            is_current=True,
        ).order_by("-created_at")
        if signal is not None
        else IntelligenceRecommendation.objects.none()
    )

    return GeneratedSectionDraft(
        indikasi_text=indikasi_text,
        analisis_text=generate_analisis_text(
            disease_name=disease.name,
            early_warning=early_warning,
            assessment=assessment,
        ),
        dampak_text=generate_dampak_text(
            disease_name=disease.name,
            early_warning=early_warning,
        ),
        upaya_text=generate_upaya_text(
            disease_name=disease.name,
            early_warning=early_warning,
        ),
        saran_tindak_text=generate_saran_tindak_text(
            disease_name=disease.name,
            recommendations=recommendations,
        ),
        source_article_ids=used_ids,
    )
