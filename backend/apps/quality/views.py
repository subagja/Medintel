import csv
import io
import json
import zipfile
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.accounts.permissions import Roles, has_role, require_role, require_role_by_method
from apps.assessments.models import ArticleValidationAssessment, EarlyWarning
from apps.entities.models import ExtractionReviewLog
from apps.signals.models import SignalHistory

from .criteria import UAT_TASKS, criteria_for
from .forms import EvaluationCompletionForm, EvaluationRecordForm, SnapshotForm
from .models import EvaluationRecord, QualityMetricSnapshot
from .services import (
    build_quality_report,
    complete_evaluation,
    completion_blockers,
    create_snapshot,
    save_evaluation,
)


def _period(request):
    today = timezone.localdate()
    period_end = parse_date(request.GET.get("date_to", "")) or today
    period_start = parse_date(request.GET.get("date_from", "")) or (
        period_end - timedelta(days=29)
    )
    if period_end < period_start:
        period_start, period_end = period_end, period_start
    return period_start, period_end


def _workspace_url(*, period_start=None, period_end=None, evaluation=None, new=None):
    url = reverse("dashboard:quality-evaluation")
    params = []
    if period_start:
        params.append(f"date_from={period_start.isoformat()}")
    if period_end:
        params.append(f"date_to={period_end.isoformat()}")
    if evaluation:
        params.append(f"evaluation={evaluation.pk}")
        params.append("tab=human")
    if new:
        params.append(f"new={new}")
        params.append("tab=human")
    return f"{url}?{'&'.join(params)}" if params else url


def _evaluation_queryset():
    return EvaluationRecord.objects.select_related(
        "created_by", "completed_by"
    ).prefetch_related("history")


@require_role_by_method(read_roles=Roles.ALL, write_roles=Roles.CONTRIBUTORS)
def quality_evaluation_workspace(request: HttpRequest) -> HttpResponse:
    period_start, period_end = _period(request)
    report = build_quality_report(period_start, period_end)
    evaluations = _evaluation_queryset()
    selected = None
    new_type = request.GET.get("new", "")

    selected_id = request.GET.get("evaluation")
    if request.method == "POST":
        selected_id = request.POST.get("evaluation_id")
        new_type = request.POST.get("evaluation_type", new_type)
    if selected_id:
        selected = get_object_or_404(evaluations, pk=selected_id)

    allowed_types = {
        EvaluationRecord.EvaluationType.EXPERT,
        EvaluationRecord.EvaluationType.UAT,
    }
    if new_type not in allowed_types:
        new_type = ""

    evaluation_form = None
    if selected:
        evaluation_form = EvaluationRecordForm(
            instance=selected,
            evaluation_type=selected.evaluation_type,
        )
    elif new_type:
        evaluation_form = EvaluationRecordForm(
            evaluation_type=new_type,
            initial={"evaluation_type": new_type},
        )
    completion_form = EvaluationCompletionForm()
    snapshot_form = SnapshotForm(
        initial={
            "snapshot_title": (
                f"Evaluasi MedIntel {period_start:%d-%m-%Y} s.d. "
                f"{period_end:%d-%m-%Y}"
            )
        }
    )

    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "create_snapshot":
                snapshot_form = SnapshotForm(request.POST)
                if snapshot_form.is_valid():
                    snapshot = create_snapshot(
                        title=snapshot_form.cleaned_data["snapshot_title"],
                        period_start=period_start,
                        period_end=period_end,
                        report=report,
                        actor=request.user,
                    )
                    messages.success(
                        request, f"Snapshot {snapshot.code} berhasil disimpan."
                    )
                    return redirect(
                        _workspace_url(
                            period_start=period_start, period_end=period_end
                        )
                    )

            elif action == "create_evaluation":
                evaluation_form = EvaluationRecordForm(
                    request.POST,
                    evaluation_type=new_type,
                )
                if evaluation_form.is_valid():
                    evaluation = save_evaluation(
                        form=evaluation_form, actor=request.user
                    )
                    messages.success(
                        request, f"Draf {evaluation.code} berhasil dibuat."
                    )
                    return redirect(
                        _workspace_url(
                            period_start=period_start,
                            period_end=period_end,
                            evaluation=evaluation,
                        )
                    )

            elif action == "update_evaluation":
                if not selected:
                    raise ValidationError("Evaluasi tidak ditemukan.")
                evaluation_form = EvaluationRecordForm(
                    request.POST,
                    instance=selected,
                    evaluation_type=selected.evaluation_type,
                )
                if evaluation_form.is_valid():
                    selected = save_evaluation(
                        form=evaluation_form,
                        actor=request.user,
                        evaluation=selected,
                    )
                    messages.success(request, f"{selected.code} diperbarui.")
                    return redirect(
                        _workspace_url(
                            period_start=period_start,
                            period_end=period_end,
                            evaluation=selected,
                        )
                    )

            elif action == "complete_evaluation":
                if not selected:
                    raise ValidationError("Evaluasi tidak ditemukan.")
                if not has_role(request.user, *Roles.APPROVERS):
                    raise PermissionDenied(
                        "Pengesahan evaluasi memerlukan Reviewer atau Admin."
                    )
                completion_form = EvaluationCompletionForm(request.POST)
                if completion_form.is_valid():
                    selected = complete_evaluation(
                        evaluation=selected,
                        actor=request.user,
                        notes=completion_form.cleaned_data["decision_notes"],
                    )
                    messages.success(
                        request, f"{selected.code} disahkan dan dikunci."
                    )
                    return redirect(
                        _workspace_url(
                            period_start=period_start,
                            period_end=period_end,
                            evaluation=selected,
                        )
                    )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))

    selected_rows = []
    selected_task_rows = []
    if selected:
        definitions = {
            item["code"]: item for item in criteria_for(selected.evaluation_type)
        }
        selected_rows = [
            {
                **definition,
                "score": (selected.scores or {}).get(code),
                "notes": (selected.criterion_notes or {}).get(code, ""),
            }
            for code, definition in definitions.items()
        ]
        if selected.evaluation_type == EvaluationRecord.EvaluationType.UAT:
            selected_task_rows = [
                {
                    "code": code,
                    "label": label,
                    "result": (selected.task_results or {}).get(
                        code, "not_tested"
                    ),
                }
                for code, label in UAT_TASKS
            ]

    return render(
        request,
        "quality/quality_evaluation_workspace.html",
        {
            "page_title": "Pusat Mutu dan Evaluasi Tesis",
            "active_menu": "quality_evaluation",
            "period_start": period_start,
            "period_end": period_end,
            "report": report,
            "sections": report["sections"],
            "narrative": report["narrative"],
            "snapshots": QualityMetricSnapshot.objects.select_related(
                "created_by"
            )[:10],
            "evaluations": evaluations[:100],
            "selected": selected,
            "selected_rows": selected_rows,
            "selected_task_rows": selected_task_rows,
            "new_type": new_type,
            "evaluation_form": evaluation_form,
            "completion_form": completion_form,
            "completion_blockers": completion_blockers(selected) if selected else [],
            "snapshot_form": snapshot_form,
            "can_approve": has_role(request.user, *Roles.APPROVERS),
            "active_tab": request.GET.get("tab", "metrics"),
        },
    )


def _csv_bytes(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _add_csv(archive, name, headers, rows):
    archive.writestr(name, _csv_bytes(headers, rows))


@require_role(*Roles.ALL)
def quality_dataset_export(request: HttpRequest) -> HttpResponse:
    period_start, period_end = _period(request)
    report = build_quality_report(period_start, period_end)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        metric_rows = []
        for section in report["sections"]:
            for metric in section["metrics"]:
                metric_rows.append(
                    [
                        section["title"],
                        metric["code"],
                        metric["label"],
                        metric["value"],
                        metric["unit"],
                        metric["numerator"],
                        metric["denominator"],
                        metric["note"],
                        period_start,
                        period_end,
                    ]
                )
        _add_csv(
            archive,
            "01_ringkasan_metrik.csv",
            [
                "bagian", "kode_metrik", "metrik", "nilai", "satuan",
                "numerator", "denominator", "catatan", "periode_awal", "periode_akhir",
            ],
            metric_rows,
        )

        assessments = ArticleValidationAssessment.objects.filter(
            article__crawled_at__date__range=(period_start, period_end)
        ).select_related("article__source", "evaluated_by")
        _add_csv(
            archive,
            "02_validasi_artikel.csv",
            [
                "article_id", "judul", "sumber", "terbit", "terkumpul",
                "status_validasi", "reliabilitas_sumber", "kredibilitas_informasi",
                "kode_admiralty", "evaluator", "diperbarui",
            ],
            (
                [
                    item.article_id,
                    item.article.title,
                    item.article.source.name,
                    item.article.published_at,
                    item.article.crawled_at,
                    item.validation_status,
                    item.source_reliability,
                    item.information_credibility,
                    item.admiralty_code,
                    item.evaluated_by or "",
                    item.updated_at,
                ]
                for item in assessments
            ),
        )

        extraction_logs = ExtractionReviewLog.objects.filter(
            reviewed_at__date__range=(period_start, period_end)
        ).select_related("reviewer")
        _add_csv(
            archive,
            "03_review_ekstraksi.csv",
            [
                "object_type", "object_id", "aksi", "reviewer", "waktu",
                "sebelum", "sesudah", "catatan",
            ],
            (
                [
                    item.object_type,
                    item.object_id,
                    item.action,
                    item.reviewer or "",
                    item.reviewed_at,
                    json.dumps(item.before_data, ensure_ascii=False),
                    json.dumps(item.after_data, ensure_ascii=False),
                    item.notes,
                ]
                for item in extraction_logs
            ),
        )

        signal_histories = SignalHistory.objects.filter(
            changed_at__date__range=(period_start, period_end)
        ).select_related("signal", "changed_by")
        _add_csv(
            archive,
            "04_keputusan_sinyal.csv",
            ["kode_sinyal", "status_awal", "status_akhir", "aktor", "waktu", "alasan"],
            (
                [
                    item.signal.code,
                    item.from_status,
                    item.to_status,
                    item.changed_by or "",
                    item.changed_at,
                    item.reason,
                ]
                for item in signal_histories
            ),
        )

        warnings = EarlyWarning.objects.filter(
            issued_at__date__range=(period_start, period_end)
        ).select_related("signal", "issued_by")
        _add_csv(
            archive,
            "05_timeline_peringatan.csv",
            [
                "kode_peringatan", "kode_sinyal", "level", "sinyal_terdeteksi",
                "peringatan_terbit", "jam_sinyal_ke_peringatan", "penerbit",
            ],
            (
                [
                    warning.code,
                    warning.signal.code,
                    warning.level,
                    warning.signal.first_detected_at,
                    warning.issued_at,
                    round(
                        (warning.issued_at - warning.signal.first_detected_at).total_seconds()
                        / 3600,
                        2,
                    ),
                    warning.issued_by or "",
                ]
                for warning in warnings
            ),
        )

        evaluations = EvaluationRecord.objects.filter(
            evaluation_date__range=(period_start, period_end)
        )
        evaluation_rows = []
        for evaluation in evaluations:
            for criterion in criteria_for(evaluation.evaluation_type):
                code = criterion["code"]
                evaluation_rows.append(
                    [
                        evaluation.code,
                        evaluation.evaluation_type,
                        evaluation.status,
                        evaluation.evaluator_name,
                        evaluation.evaluator_role,
                        evaluation.institution,
                        evaluation.evaluation_date,
                        criterion["dimension"],
                        code,
                        criterion["label"],
                        (evaluation.scores or {}).get(code, ""),
                        (evaluation.criterion_notes or {}).get(code, ""),
                        evaluation.average_score,
                    ]
                )
        _add_csv(
            archive,
            "06_validasi_ahli_uat.csv",
            [
                "kode", "jenis", "status", "evaluator", "peran_keahlian", "instansi",
                "tanggal", "dimensi", "kode_kriteria", "kriteria", "skor", "catatan",
                "rerata_evaluasi",
            ],
            evaluation_rows,
        )
        _add_csv(
            archive,
            "07_skenario_uat.csv",
            [
                "kode", "evaluator", "tanggal", "kode_skenario", "skenario", "hasil",
            ],
            (
                [
                    evaluation.code,
                    evaluation.evaluator_name,
                    evaluation.evaluation_date,
                    code,
                    label,
                    (evaluation.task_results or {}).get(code, "not_tested"),
                ]
                for evaluation in evaluations.filter(
                    evaluation_type=EvaluationRecord.EvaluationType.UAT
                )
                for code, label in UAT_TASKS
            ),
        )
        archive.writestr("08_ringkasan_bab_iv.txt", report["narrative"].encode("utf-8"))

    filename = f"dataset-evaluasi-medintel-{period_start}-{period_end}.zip"
    response = HttpResponse(output.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_role(*Roles.ALL)
def quality_bab4_export(request: HttpRequest) -> HttpResponse:
    period_start, period_end = _period(request)
    report = build_quality_report(period_start, period_end)
    response = HttpResponse(
        report["narrative"], content_type="text/plain; charset=utf-8"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="ringkasan-bab-iv-{period_start}-{period_end}.txt"'
    )
    return response
