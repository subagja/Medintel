from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.signals.models import Signal, SignalArticle

from ..models import (
    ArticleValidationAssessment,
    IntelligenceReport,
    IntelligenceReportHistory,
    IntelligenceReportSection,
    SignalAssessment,
)
from .report_generation import generate_signal_section_draft


REPORTABLE_SIGNAL_STATUSES = (
    Signal.Status.VALIDATED,
    Signal.Status.CORRECTED,
    Signal.Status.ESCALATED,
)


@dataclass(frozen=True)
class ReportEligibility:
    is_eligible: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class ReportCompleteness:
    is_complete: bool
    issues: tuple[str, ...]
    completed_fields: int
    total_fields: int

    @property
    def percentage(self) -> int:
        if not self.total_fields:
            return 0
        return round((self.completed_fields / self.total_fields) * 100)


def eligible_report_signals():
    """Sinyal yang memiliki assessment selesai dan bukti artikel valid."""
    return (
        Signal.objects.filter(
            status__in=REPORTABLE_SIGNAL_STATUSES,
            assessments__is_current=True,
            assessments__status=SignalAssessment.Status.COMPLETED,
            signal_articles__support_type__in=[
                SignalArticle.SupportType.PRIMARY,
                SignalArticle.SupportType.SUPPORTING,
                SignalArticle.SupportType.CORROBORATING,
            ],
            signal_articles__article__validation_assessment__validation_status=(
                ArticleValidationAssessment.ValidationStatus.VALIDATED
            ),
        )
        .select_related("primary_disease", "primary_location")
        .prefetch_related("assessments", "early_warnings")
        .distinct()
        .order_by("-priority_level", "-last_updated_at")
    )


def evaluate_report_signal(signal: Signal) -> ReportEligibility:
    blockers = []
    if signal.status not in REPORTABLE_SIGNAL_STATUSES:
        blockers.append("Sinyal belum tervalidasi, dikoreksi, atau dieskalasi.")

    if not signal.assessments.filter(
        is_current=True,
        status=SignalAssessment.Status.COMPLETED,
    ).exists():
        blockers.append("Assessment aktif belum selesai.")

    has_valid_article = signal.signal_articles.exclude(
        support_type=SignalArticle.SupportType.CONTRADICTING,
    ).filter(
        article__validation_assessment__validation_status=(
            ArticleValidationAssessment.ValidationStatus.VALIDATED
        )
    ).exists()
    if not has_valid_article:
        blockers.append("Belum ada artikel pendukung yang tervalidasi.")

    return ReportEligibility(
        is_eligible=not blockers,
        blockers=tuple(blockers),
    )


def _next_report_code() -> str:
    year = timezone.localdate().year
    prefix = f"LI-{year}-"
    highest = 0
    for code in IntelligenceReport.objects.filter(
        code__startswith=prefix,
    ).values_list("code", flat=True):
        try:
            highest = max(highest, int(code.rsplit("-", 1)[1]))
        except (AttributeError, IndexError, TypeError, ValueError):
            continue
    return f"{prefix}{highest + 1:04d}"


def _default_subject(signals: list[Signal]) -> str:
    if len(signals) == 1:
        signal = signals[0]
        return (
            f"Perkembangan {signal.primary_disease.name} di "
            f"{signal.primary_location.name}"
        )
    return "Perkembangan Situasi Ancaman Penyakit Menular di Indonesia"


@transaction.atomic
def create_intelligence_report(
    *,
    signals,
    created_by,
    kepada: str,
    dari: str,
    tembusan: str,
    hal: str,
    nilai: str,
    report_date,
    signature_block: str,
) -> IntelligenceReport:
    signal_ids = [signal.pk for signal in signals]
    if not signal_ids:
        raise ValidationError("Pilih minimal satu sinyal tervalidasi.")
    if len(signal_ids) > 10:
        raise ValidationError("Satu laporan maksimal memuat 10 sinyal.")

    locked_signals = list(
        Signal.objects.select_for_update()
        .select_related("primary_disease", "primary_location")
        .filter(pk__in=signal_ids)
        .order_by("-priority_level", "-last_updated_at")
    )
    if len(locked_signals) != len(set(signal_ids)):
        raise ValidationError("Sebagian sinyal yang dipilih tidak ditemukan.")

    blockers = []
    for signal in locked_signals:
        eligibility = evaluate_report_signal(signal)
        blockers.extend(
            f"{signal.code}: {blocker}" for blocker in eligibility.blockers
        )
    if blockers:
        raise ValidationError(blockers)

    report = IntelligenceReport.objects.create(
        code=_next_report_code(),
        kepada=kepada.strip() or "Yth. Pimpinan",
        dari=dari.strip(),
        tembusan=tembusan.strip(),
        hal=hal.strip() or _default_subject(locked_signals),
        nilai=nilai.strip(),
        report_date=report_date,
        signature_block=signature_block.strip(),
        created_by=created_by,
        updated_by=created_by,
    )

    section_metadata = []
    for order, signal in enumerate(locked_signals):
        draft = generate_signal_section_draft(signal=signal)
        section = IntelligenceReportSection.objects.create(
            report=report,
            order=order,
            disease=signal.primary_disease,
            signal=signal,
            assessment_version=draft.assessment_version,
            warning_version=draft.warning_version,
            recommendation_version=draft.recommendation_version,
            indikasi_text=draft.indikasi_text,
            analisis_text=draft.analisis_text,
            dampak_text=draft.dampak_text,
            upaya_text=draft.upaya_text,
            saran_tindak_text=draft.saran_tindak_text,
        )
        section.source_articles.set(draft.source_article_ids)
        section_metadata.append(
            {
                "signal": signal.code,
                "assessment_version": draft.assessment_version,
                "warning_version": draft.warning_version,
                "recommendation_version": draft.recommendation_version,
                "article_count": len(draft.source_article_ids),
            }
        )

    IntelligenceReportHistory.objects.create(
        report=report,
        action=IntelligenceReportHistory.Action.CREATED,
        from_status="",
        to_status=report.status,
        notes="Draf laporan dibentuk dari sinyal tervalidasi.",
        metadata={"sections": section_metadata},
        changed_by=created_by,
    )
    return report


def report_completeness(report: IntelligenceReport) -> ReportCompleteness:
    issues = []
    completed = 0
    required_header = (
        (report.kepada, "Kepada belum diisi."),
        (report.dari, "Dari belum diisi."),
        (report.hal, "Hal belum diisi."),
        (report.nilai, "Nilai informasi belum diisi."),
        (report.signature_block, "Blok tanda tangan belum diisi."),
    )
    total = len(required_header)
    for value, message in required_header:
        if value and value.strip():
            completed += 1
        else:
            issues.append(message)

    sections = list(report.sections.all())
    if not sections:
        issues.append("Laporan belum memiliki poin pembahasan.")
        total += 1
    else:
        for section in sections:
            fields = (
                (section.signal_id, f"Poin {section.letter} belum terhubung ke sinyal."),
                (
                    section.source_articles.exists(),
                    f"Poin {section.letter} belum memiliki artikel sumber.",
                ),
                (section.indikasi_text, f"Indikasi poin {section.letter} belum diisi."),
                (section.analisis_text, f"Analisis poin {section.letter} belum diisi."),
                (section.dampak_text, f"Dampak poin {section.letter} belum diisi."),
                (section.upaya_text, f"Upaya poin {section.letter} belum diisi."),
                (
                    section.saran_tindak_text,
                    f"Saran tindak poin {section.letter} belum diisi.",
                ),
            )
            total += len(fields)
            for value, message in fields:
                is_filled = bool(value.strip()) if isinstance(value, str) else bool(value)
                if is_filled:
                    completed += 1
                else:
                    issues.append(message)

    return ReportCompleteness(
        is_complete=not issues,
        issues=tuple(issues),
        completed_fields=completed,
        total_fields=total,
    )


@transaction.atomic
def save_report_draft(*, report: IntelligenceReport, actor, notes: str = ""):
    locked = IntelligenceReport.objects.select_for_update().get(pk=report.pk)
    if locked.status != IntelligenceReport.Status.DRAFT:
        raise ValidationError("Laporan yang sudah final tidak dapat disunting.")

    locked.kepada = report.kepada.strip()
    locked.dari = report.dari.strip()
    locked.tembusan = report.tembusan.strip()
    locked.hal = report.hal.strip()
    locked.nilai = report.nilai.strip()
    locked.signature_block = report.signature_block.strip()
    locked.updated_by = actor
    locked.save(
        update_fields=[
            "kepada",
            "dari",
            "tembusan",
            "hal",
            "nilai",
            "signature_block",
            "updated_by",
            "updated_at",
        ]
    )

    IntelligenceReportHistory.objects.create(
        report=locked,
        action=IntelligenceReportHistory.Action.UPDATED,
        from_status=locked.status,
        to_status=locked.status,
        notes=notes.strip() or "Isi draf laporan diperbarui.",
        changed_by=actor,
    )
    return locked


@transaction.atomic
def transition_report(
    *,
    report: IntelligenceReport,
    target_status: str,
    actor,
    notes: str,
) -> IntelligenceReport:
    locked = (
        IntelligenceReport.objects.select_for_update()
        .prefetch_related("sections__source_articles")
        .get(pk=report.pk)
    )
    cleaned_notes = notes.strip()
    if not cleaned_notes:
        raise ValidationError("Catatan keputusan wajib diisi.")

    expected = {
        IntelligenceReport.Status.FINAL: IntelligenceReport.Status.DRAFT,
        IntelligenceReport.Status.DISTRIBUTED: IntelligenceReport.Status.FINAL,
        IntelligenceReport.Status.ARCHIVED: IntelligenceReport.Status.DISTRIBUTED,
    }
    if target_status not in expected:
        raise ValidationError("Tujuan status laporan tidak valid.")
    if locked.status != expected[target_status]:
        raise ValidationError(
            "Urutan status wajib Draf → Final → Didistribusikan → Arsip."
        )

    now = timezone.now()
    from_status = locked.status
    action = None
    update_fields = ["status", "updated_by", "updated_at"]
    locked.status = target_status
    locked.updated_by = actor

    if target_status == IntelligenceReport.Status.FINAL:
        completeness = report_completeness(locked)
        if not completeness.is_complete:
            raise ValidationError(list(completeness.issues))
        locked.review_notes = cleaned_notes
        locked.finalized_by = actor
        locked.finalized_at = now
        action = IntelligenceReportHistory.Action.FINALIZED
        update_fields.extend(["review_notes", "finalized_by", "finalized_at"])
    elif target_status == IntelligenceReport.Status.DISTRIBUTED:
        locked.distribution_notes = cleaned_notes
        locked.distributed_by = actor
        locked.distributed_at = now
        action = IntelligenceReportHistory.Action.DISTRIBUTED
        update_fields.extend(
            ["distribution_notes", "distributed_by", "distributed_at"]
        )
    else:
        locked.archived_by = actor
        locked.archived_at = now
        action = IntelligenceReportHistory.Action.ARCHIVED
        update_fields.extend(["archived_by", "archived_at"])

    locked.save(update_fields=update_fields)
    IntelligenceReportHistory.objects.create(
        report=locked,
        action=action,
        from_status=from_status,
        to_status=target_status,
        notes=cleaned_notes,
        changed_by=actor,
    )
    return locked
