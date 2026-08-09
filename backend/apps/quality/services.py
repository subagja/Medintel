from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from statistics import median

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.articles.models import Article
from apps.assessments.models import (
    ArticleValidationAssessment,
    EarlyWarning,
    IntelligenceRecommendation,
    SignalAssessment,
)
from apps.collection.models import CollectionJobItem
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    ValidationStatus,
)
from apps.requirements.models import IntelligenceRequirement
from apps.signals.models import Signal, SignalHistory

from .criteria import UAT_TASKS, criteria_for
from .models import EvaluationHistory, EvaluationRecord, QualityMetricSnapshot


def _rate(numerator: int, denominator: int):
    if not denominator:
        return None
    return round((numerator / denominator) * 100, 1)


def _metric(code, label, value, unit, *, numerator=None, denominator=None, note=""):
    if value is None:
        display = "Belum tersedia"
    elif unit == "%":
        display = f"{value:.1f}%"
    elif unit == "jam":
        display = f"{value:.1f} jam"
    elif isinstance(value, float):
        display = f"{value:.2f}"
    else:
        display = str(value)
    return {
        "code": code,
        "label": label,
        "value": value,
        "display": display,
        "unit": unit,
        "numerator": numerator,
        "denominator": denominator,
        "note": note,
    }


def _date_filter(queryset, field: str, period_start: date, period_end: date):
    return queryset.filter(
        **{
            f"{field}__date__gte": period_start,
            f"{field}__date__lte": period_end,
        }
    )


def _entity_metrics(model, prefix, label, period_start, period_end):
    queryset = _date_filter(
        model.objects.exclude(validated_at__isnull=True),
        "validated_at",
        period_start,
        period_end,
    )
    counts = Counter(queryset.values_list("validation_status", flat=True))
    reviewed = sum(
        counts[status]
        for status in (
            ValidationStatus.VALIDATED,
            ValidationStatus.CORRECTED,
            ValidationStatus.REJECTED,
        )
    )
    exact = counts[ValidationStatus.VALIDATED]
    corrected = counts[ValidationStatus.CORRECTED]
    accepted = exact + corrected
    return [
        _metric(
            f"{prefix}_reviewed",
            f"{label} ditinjau analis",
            reviewed,
            "objek",
        ),
        _metric(
            f"{prefix}_exact_rate",
            f"Ketepatan {label.lower()} tanpa koreksi",
            _rate(exact, reviewed),
            "%",
            numerator=exact,
            denominator=reviewed,
            note="Proporsi hasil ekstraksi yang divalidasi tanpa koreksi dari seluruh objek yang ditinjau.",
        ),
        _metric(
            f"{prefix}_correction_rate",
            f"Tingkat koreksi {label.lower()}",
            _rate(corrected, reviewed),
            "%",
            numerator=corrected,
            denominator=reviewed,
        ),
        _metric(
            f"{prefix}_acceptance_rate",
            f"Penerimaan {label.lower()} setelah review",
            _rate(accepted, reviewed),
            "%",
            numerator=accepted,
            denominator=reviewed,
            note="Mencakup objek yang langsung tervalidasi dan objek yang diterima setelah dikoreksi.",
        ),
    ]


def _hours_between(start: datetime | None, end: datetime | None):
    if not start or not end or end < start:
        return None
    return (end - start).total_seconds() / 3600


def _median_metric(code, label, values, note=""):
    clean = [value for value in values if value is not None]
    return _metric(
        code,
        label,
        round(median(clean), 1) if clean else None,
        "jam",
        denominator=len(clean),
        note=(note + (" " if note else "") + f"Sampel waktu: {len(clean)}.").strip(),
    )


def _signal_decisions(period_start, period_end):
    histories = _date_filter(
        SignalHistory.objects.filter(
            to_status__in={
                Signal.Status.VALIDATED,
                Signal.Status.CORRECTED,
                Signal.Status.REJECTED,
            }
        ).order_by("signal_id", "-changed_at"),
        "changed_at",
        period_start,
        period_end,
    )
    latest = {}
    for history in histories:
        latest.setdefault(history.signal_id, history.to_status)
    return Counter(latest.values())


def build_quality_report(period_start: date, period_end: date):
    if period_end < period_start:
        raise ValidationError("Tanggal akhir tidak boleh sebelum tanggal awal.")

    collection_items = _date_filter(
        CollectionJobItem.objects.all(), "created_at", period_start, period_end
    )
    collection_counts = Counter(collection_items.values_list("status", flat=True))
    total_candidates = collection_items.count()
    created = collection_counts[CollectionJobItem.Status.CREATED]
    failed = (
        collection_counts[CollectionJobItem.Status.FAILED]
        + collection_counts[CollectionJobItem.Status.FETCH_BLOCKED]
    )

    article_assessments = _date_filter(
        ArticleValidationAssessment.objects.all(),
        "article__crawled_at",
        period_start,
        period_end,
    )
    article_counts = Counter(
        article_assessments.values_list("validation_status", flat=True)
    )
    article_reviewed = (
        article_counts[ArticleValidationAssessment.ValidationStatus.VALIDATED]
        + article_counts[ArticleValidationAssessment.ValidationStatus.REJECTED]
    )
    article_validated = article_counts[
        ArticleValidationAssessment.ValidationStatus.VALIDATED
    ]

    decision_counts = _signal_decisions(period_start, period_end)
    accepted_signals = (
        decision_counts[Signal.Status.VALIDATED]
        + decision_counts[Signal.Status.CORRECTED]
    )
    reviewed_signals = accepted_signals + decision_counts[Signal.Status.REJECTED]

    completed_assessments = _date_filter(
        SignalAssessment.objects.filter(status=SignalAssessment.Status.COMPLETED),
        "completed_at",
        period_start,
        period_end,
    )
    warnings = _date_filter(
        EarlyWarning.objects.all(), "issued_at", period_start, period_end
    ).select_related("signal")
    recommendations = _date_filter(
        IntelligenceRecommendation.objects.all(),
        "created_at",
        period_start,
        period_end,
    )
    actionable_statuses = {
        IntelligenceRecommendation.Status.APPROVED,
        IntelligenceRecommendation.Status.IN_PROGRESS,
        IntelligenceRecommendation.Status.COMPLETED,
    }
    actionable = recommendations.filter(status__in=actionable_statuses).count()
    completed_recommendations = recommendations.filter(
        status=IntelligenceRecommendation.Status.COMPLETED
    ).count()
    high_warnings = warnings.filter(
        level__in={EarlyWarning.Level.HIGH, EarlyWarning.Level.CRITICAL}
    )
    high_warning_ids = set(high_warnings.values_list("id", flat=True))
    high_with_action = recommendations.filter(
        early_warning_id__in=high_warning_ids,
        status__in=actionable_statuses,
    ).values("early_warning_id").distinct().count()

    requirement_qs = IntelligenceRequirement.objects.filter(
        created_at__date__lte=period_end
    ).exclude(status=IntelligenceRequirement.Status.DRAFT)
    answered_requirements = requirement_qs.filter(
        status__in={
            IntelligenceRequirement.Status.ANSWERED,
            IntelligenceRequirement.Status.CLOSED,
        }
    ).count()

    publication_to_collection = []
    collection_to_validation = []
    articles = _date_filter(
        Article.objects.exclude(published_at__isnull=True),
        "crawled_at",
        period_start,
        period_end,
    ).select_related("validation_assessment")
    for article in articles:
        publication_to_collection.append(
            _hours_between(article.published_at, article.crawled_at)
        )
        try:
            assessment = article.validation_assessment
        except ArticleValidationAssessment.DoesNotExist:
            assessment = None
        if assessment and assessment.validation_status != ArticleValidationAssessment.ValidationStatus.PENDING:
            collection_to_validation.append(
                _hours_between(article.crawled_at, assessment.updated_at)
            )

    signal_to_warning = []
    publication_to_warning = []
    for warning in warnings.prefetch_related("signal__signal_articles__article"):
        signal_to_warning.append(
            _hours_between(warning.signal.first_detected_at, warning.issued_at)
        )
        publication_dates = [
            link.article.published_at
            for link in warning.signal.signal_articles.all()
            if link.article.published_at and link.article.published_at <= warning.issued_at
        ]
        if publication_dates:
            publication_to_warning.append(
                _hours_between(min(publication_dates), warning.issued_at)
            )

    completed_evaluations = EvaluationRecord.objects.filter(
        status=EvaluationRecord.Status.COMPLETED,
        evaluation_date__range=(period_start, period_end),
    )
    expert_records = list(
        completed_evaluations.filter(
            evaluation_type=EvaluationRecord.EvaluationType.EXPERT
        )
    )
    uat_records = list(
        completed_evaluations.filter(
            evaluation_type=EvaluationRecord.EvaluationType.UAT
        )
    )

    def average_record_score(records):
        values = [record.average_score for record in records if record.average_score]
        return round(sum(values) / len(values), 2) if values else None

    uat_tasks = Counter()
    for record in uat_records:
        uat_tasks.update((record.task_results or {}).values())
    tested_tasks = uat_tasks["passed"] + uat_tasks["failed"]

    sections = [
        {
            "code": "collection",
            "title": "Mutu Pengumpulan",
            "metrics": [
                _metric("collection_candidates", "Kandidat diperiksa", total_candidates, "URL"),
                _metric("collection_created", "Artikel baru dibuat", created, "artikel"),
                _metric(
                    "collection_yield_rate",
                    "Yield artikel baru",
                    _rate(created, total_candidates),
                    "%",
                    numerator=created,
                    denominator=total_candidates,
                    note="Yield dipengaruhi duplikat dan filter relevansi; bukan ukuran akurasi ekstraksi.",
                ),
                _metric(
                    "collection_duplicate_rate",
                    "Tingkat duplikat",
                    _rate(collection_counts[CollectionJobItem.Status.DUPLICATE], total_candidates),
                    "%",
                    numerator=collection_counts[CollectionJobItem.Status.DUPLICATE],
                    denominator=total_candidates,
                ),
                _metric(
                    "collection_failure_rate",
                    "Kegagalan teknis pengambilan",
                    _rate(failed, total_candidates),
                    "%",
                    numerator=failed,
                    denominator=total_candidates,
                ),
            ],
        },
        {
            "code": "data_quality",
            "title": "Mutu Data dan Ekstraksi",
            "metrics": [
                _metric("article_reviewed", "Artikel selesai ditinjau", article_reviewed, "artikel"),
                _metric(
                    "article_validity_rate",
                    "Artikel diterima setelah validasi",
                    _rate(article_validated, article_reviewed),
                    "%",
                    numerator=article_validated,
                    denominator=article_reviewed,
                ),
                *_entity_metrics(ArticleDisease, "disease", "Penyakit", period_start, period_end),
                *_entity_metrics(ArticleLocation, "location", "Lokasi/geocoding", period_start, period_end),
                *_entity_metrics(ArticleFact, "fact", "Fakta", period_start, period_end),
            ],
        },
        {
            "code": "effectiveness",
            "title": "Efektivitas Intelijen",
            "metrics": [
                _metric("signal_reviewed", "Sinyal diputuskan", reviewed_signals, "sinyal"),
                _metric(
                    "signal_validity_rate",
                    "Validitas kandidat sinyal",
                    _rate(accepted_signals, reviewed_signals),
                    "%",
                    numerator=accepted_signals,
                    denominator=reviewed_signals,
                    note="Proporsi keputusan sinyal yang tervalidasi/dikoreksi dibanding seluruh keputusan terima/tolak.",
                ),
                _metric("assessments_completed", "Assessment selesai", completed_assessments.count(), "assessment"),
                _metric("warnings_issued", "Peringatan diterbitkan", warnings.count(), "peringatan"),
                _metric(
                    "actionability_rate",
                    "Rekomendasi telah ditetapkan/ditindaklanjuti",
                    _rate(actionable, recommendations.count()),
                    "%",
                    numerator=actionable,
                    denominator=recommendations.count(),
                ),
                _metric(
                    "recommendation_completion_rate",
                    "Rekomendasi selesai ditindaklanjuti",
                    _rate(completed_recommendations, actionable),
                    "%",
                    numerator=completed_recommendations,
                    denominator=actionable,
                ),
                _metric(
                    "preparedness_coverage",
                    "Peringatan tinggi/kritis dengan tindak lanjut",
                    _rate(high_with_action, high_warnings.count()),
                    "%",
                    numerator=high_with_action,
                    denominator=high_warnings.count(),
                ),
                _metric(
                    "requirement_answer_rate",
                    "Kebutuhan intelijen terjawab/ditutup",
                    _rate(answered_requirements, requirement_qs.count()),
                    "%",
                    numerator=answered_requirements,
                    denominator=requirement_qs.count(),
                ),
            ],
        },
        {
            "code": "timeliness",
            "title": "Ketepatan Waktu",
            "metrics": [
                _median_metric(
                    "publication_to_collection_hours",
                    "Median artikel terbit → terkumpul",
                    publication_to_collection,
                ),
                _median_metric(
                    "collection_to_validation_hours",
                    "Median terkumpul → tervalidasi",
                    collection_to_validation,
                ),
                _median_metric(
                    "signal_to_warning_hours",
                    "Median sinyal → peringatan dini",
                    signal_to_warning,
                ),
                _median_metric(
                    "publication_to_warning_hours",
                    "Median artikel terbit → peringatan dini",
                    publication_to_warning,
                ),
            ],
        },
        {
            "code": "human_validation",
            "title": "Validasi Ahli dan UAT",
            "metrics": [
                _metric("expert_completed", "Validasi ahli selesai", len(expert_records), "evaluator"),
                _metric("expert_average", "Rerata skor validasi ahli", average_record_score(expert_records), "skor 1–5"),
                _metric("uat_completed", "UAT selesai", len(uat_records), "pengguna"),
                _metric("uat_average", "Rerata skor UAT", average_record_score(uat_records), "skor 1–5"),
                _metric(
                    "uat_task_success_rate",
                    "Keberhasilan skenario UAT",
                    _rate(uat_tasks["passed"], tested_tasks),
                    "%",
                    numerator=uat_tasks["passed"],
                    denominator=tested_tasks,
                ),
            ],
        },
    ]

    report = {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "generated_at": timezone.now().isoformat(),
        "sections": sections,
    }
    report["metric_map"] = {
        metric["code"]: metric
        for section in sections
        for metric in section["metrics"]
    }
    report["narrative"] = build_bab4_narrative(report)
    return report


def build_bab4_narrative(report):
    metrics = report["metric_map"]

    def display(code):
        return metrics[code]["display"]

    paragraphs = [
        (
            f"Pada periode {report['period_start']} sampai {report['period_end']}, "
            f"MedIntel memeriksa {display('collection_candidates')} kandidat URL dan "
            f"menghasilkan {display('collection_created')} artikel baru. Yield artikel "
            f"baru tercatat {display('collection_yield_rate')}, sedangkan kegagalan "
            f"teknis pengambilan sebesar {display('collection_failure_rate')}."
        ),
        (
            f"Berdasarkan objek yang telah ditinjau analis, ketepatan ekstraksi tanpa "
            f"koreksi tercatat {display('disease_exact_rate')} untuk penyakit, "
            f"{display('location_exact_rate')} untuk lokasi/geocoding, dan "
            f"{display('fact_exact_rate')} untuk fakta. Angka ini harus dibaca bersama "
            f"jumlah sampel review pada masing-masing unsur dan tidak mewakili data "
            f"epidemiologis resmi."
        ),
        (
            f"Pada tahap analitis, validitas kandidat sinyal sebesar "
            f"{display('signal_validity_rate')}. Sistem menyelesaikan "
            f"{display('assessments_completed')} assessment dan menerbitkan "
            f"{display('warnings_issued')} peringatan. Median waktu artikel terbit "
            f"hingga terkumpul adalah {display('publication_to_collection_hours')}, "
            f"sedangkan median waktu sinyal hingga peringatan dini adalah "
            f"{display('signal_to_warning_hours')}."
        ),
        (
            f"Validasi manusia pada periode ini mencakup "
            f"{display('expert_completed')} validasi ahli dengan rerata "
            f"{display('expert_average')} dan {display('uat_completed')} UAT dengan "
            f"rerata {display('uat_average')}. Hasil tersebut digunakan sebagai bukti "
            f"pendukung kelayakan sistem, bukan sebagai dasar otomatis untuk menetapkan "
            f"KLB atau keputusan kebijakan."
        ),
    ]
    return "\n\n".join(paragraphs)


def _next_code(model, prefix):
    year = timezone.localdate().year
    full_prefix = f"{prefix}-{year}-"
    highest = 0
    for code in model.objects.filter(code__startswith=full_prefix).values_list(
        "code", flat=True
    ):
        try:
            highest = max(highest, int(code.rsplit("-", 1)[1]))
        except (TypeError, ValueError, IndexError):
            continue
    return f"{full_prefix}{highest + 1:04d}"


@transaction.atomic
def create_snapshot(*, title, period_start, period_end, report, actor):
    return QualityMetricSnapshot.objects.create(
        code=_next_code(QualityMetricSnapshot, "QM"),
        title=title.strip(),
        period_start=period_start,
        period_end=period_end,
        metrics=report,
        narrative=report["narrative"],
        created_by=actor,
    )


def _evaluation_metadata(evaluation):
    return {
        "scores": evaluation.scores,
        "task_results": evaluation.task_results,
        "average_score": evaluation.average_score,
    }


@transaction.atomic
def save_evaluation(*, form, actor, evaluation=None):
    if evaluation and not evaluation.is_editable:
        raise ValidationError("Evaluasi yang selesai telah dikunci.")
    instance = form.save(commit=False)
    if evaluation is None:
        prefix = "VA" if instance.evaluation_type == EvaluationRecord.EvaluationType.EXPERT else "UAT"
        instance.code = _next_code(EvaluationRecord, prefix)
        instance.created_by = actor
        action = EvaluationHistory.Action.CREATED
        from_status = ""
    else:
        action = EvaluationHistory.Action.UPDATED
        from_status = instance.status
    scores, notes, tasks = form.instrument_payload()
    instance.scores = scores
    instance.criterion_notes = notes
    instance.task_results = tasks
    instance.save()
    EvaluationHistory.objects.create(
        evaluation=instance,
        action=action,
        from_status=from_status,
        to_status=instance.status,
        metadata=_evaluation_metadata(instance),
        changed_by=actor,
    )
    return instance


def completion_blockers(evaluation):
    blockers = []
    expected_codes = {item["code"] for item in criteria_for(evaluation.evaluation_type)}
    missing_scores = expected_codes - set((evaluation.scores or {}).keys())
    if missing_scores:
        blockers.append(f"Masih ada {len(missing_scores)} kriteria yang belum diberi skor.")
    if evaluation.evaluation_type == EvaluationRecord.EvaluationType.UAT:
        task_results = evaluation.task_results or {}
        untested = [
            code
            for code, _label in UAT_TASKS
            if task_results.get(code, "not_tested") == "not_tested"
        ]
        if untested:
            blockers.append(f"Masih ada {len(untested)} skenario UAT yang belum diuji.")
    if not (evaluation.general_findings or "").strip():
        blockers.append("Temuan umum wajib diisi.")
    return blockers


@transaction.atomic
def complete_evaluation(*, evaluation, actor, notes):
    locked = EvaluationRecord.objects.select_for_update().get(pk=evaluation.pk)
    if not locked.is_editable:
        raise ValidationError("Evaluasi sudah selesai.")
    blockers = completion_blockers(locked)
    if blockers:
        raise ValidationError(" ".join(blockers))
    locked.status = EvaluationRecord.Status.COMPLETED
    locked.completed_by = actor
    locked.completed_at = timezone.now()
    locked.save(update_fields=["status", "completed_by", "completed_at", "updated_at"])
    EvaluationHistory.objects.create(
        evaluation=locked,
        action=EvaluationHistory.Action.COMPLETED,
        from_status=EvaluationRecord.Status.DRAFT,
        to_status=EvaluationRecord.Status.COMPLETED,
        notes=notes.strip(),
        metadata=_evaluation_metadata(locked),
        changed_by=actor,
    )
    return locked
