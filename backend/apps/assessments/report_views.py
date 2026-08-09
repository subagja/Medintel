from io import BytesIO
from xml.sax.saxutils import escape

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.permissions import Roles, has_role, require_role

from .models import IntelligenceReport
from .report_forms import (
    IntelligenceReportCreateForm,
    IntelligenceReportDecisionForm,
    IntelligenceReportHeaderForm,
    IntelligenceReportSectionFormSet,
)
from .services.intelligence_report import (
    create_intelligence_report,
    eligible_report_signals,
    report_completeness,
    save_report_draft,
    transition_report,
)


def _report_queryset():
    return (
        IntelligenceReport.objects.select_related(
            "created_by",
            "updated_by",
            "finalized_by",
            "distributed_by",
            "archived_by",
        )
        .prefetch_related(
            "sections__disease",
            "sections__signal__primary_location",
            "sections__source_articles__source",
            "history__changed_by",
        )
    )


@require_role(*Roles.ALL)
def report_document_list(request: HttpRequest) -> HttpResponse:
    reports = _report_queryset().annotate(section_count=Count("sections", distinct=True))

    selected_status = request.GET.get("status", "").strip()
    valid_statuses = {value for value, _label in IntelligenceReport.Status.choices}
    if selected_status in valid_statuses:
        reports = reports.filter(status=selected_status)

    query = request.GET.get("q", "").strip()
    if query:
        reports = reports.filter(
            Q(code__icontains=query)
            | Q(hal__icontains=query)
            | Q(kepada__icontains=query)
            | Q(sections__disease__name__icontains=query)
            | Q(sections__signal__primary_location__name__icontains=query)
        ).distinct()

    paginator = Paginator(reports.order_by("-report_date", "-created_at"), 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    summary = {
        "total": IntelligenceReport.objects.count(),
        "draft": IntelligenceReport.objects.filter(
            status=IntelligenceReport.Status.DRAFT
        ).count(),
        "final": IntelligenceReport.objects.filter(
            status=IntelligenceReport.Status.FINAL
        ).count(),
        "distributed": IntelligenceReport.objects.filter(
            status=IntelligenceReport.Status.DISTRIBUTED
        ).count(),
        "archived": IntelligenceReport.objects.filter(
            status=IntelligenceReport.Status.ARCHIVED
        ).count(),
        "eligible_signals": eligible_report_signals().count(),
    }

    return render(
        request,
        "assessments/intelligence_report_list.html",
        {
            "page_title": "Laporan Intelijen & Arsip",
            "active_menu": "reports",
            "page_obj": page_obj,
            "summary": summary,
            "status_choices": IntelligenceReport.Status.choices,
            "selected_status": selected_status,
            "query": query,
        },
    )


@require_role(*Roles.CONTRIBUTORS)
def report_document_create(request: HttpRequest) -> HttpResponse:
    initial = {
        "kepada": "Yth. Pimpinan",
        "report_date": timezone.localdate(),
    }
    form = IntelligenceReportCreateForm(request.POST or None, initial=initial)

    if request.method == "POST" and form.is_valid():
        try:
            report = create_intelligence_report(
                signals=form.cleaned_data["signals"],
                created_by=request.user,
                kepada=form.cleaned_data["kepada"],
                dari=form.cleaned_data["dari"],
                tembusan=form.cleaned_data["tembusan"],
                hal=form.cleaned_data["hal"],
                nilai=form.cleaned_data["nilai"],
                report_date=form.cleaned_data["report_date"],
                signature_block=form.cleaned_data["signature_block"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(
                request,
                f"Draf {report.code} berhasil dibentuk dari sinyal tervalidasi.",
            )
            return redirect("dashboard:report-document-edit", report_id=report.pk)

    return render(
        request,
        "assessments/intelligence_report_create.html",
        {
            "page_title": "Buat Laporan Intelijen",
            "active_menu": "reports",
            "form": form,
            "eligible_count": form.fields["signals"].queryset.count(),
        },
    )


def _require_action_role(user, action: str) -> None:
    required_roles = (
        Roles.APPROVERS
        if action in {"finalize", "distribute", "archive"}
        else Roles.CONTRIBUTORS
    )
    if not has_role(user, *required_roles):
        raise PermissionDenied("Peran Anda tidak diizinkan menjalankan aksi ini.")


@require_role(*Roles.ALL)
def report_document_edit(request: HttpRequest, report_id) -> HttpResponse:
    report = get_object_or_404(_report_queryset(), pk=report_id)
    section_queryset = report.sections.order_by("order")
    editable = report.status == IntelligenceReport.Status.DRAFT

    header_form = IntelligenceReportHeaderForm(
        request.POST or None,
        instance=report,
        prefix="header",
    )
    section_formset = IntelligenceReportSectionFormSet(
        request.POST or None,
        queryset=section_queryset,
        prefix="sections",
    )
    decision_form = IntelligenceReportDecisionForm(
        request.POST or None,
        prefix="decision",
    )

    can_contribute = has_role(request.user, *Roles.CONTRIBUTORS)
    can_approve = has_role(request.user, *Roles.APPROVERS)
    if not editable or not can_contribute:
        for field in header_form.fields.values():
            field.disabled = True
        for section_form in section_formset.forms:
            for field in section_form.fields.values():
                field.disabled = True

    if request.method == "POST":
        action = request.POST.get("action", "save")
        _require_action_role(request.user, action)

        if action in {"save", "finalize"}:
            if not editable:
                raise PermissionDenied("Laporan yang sudah final terkunci.")

            forms_valid = header_form.is_valid() and section_formset.is_valid()
            if action == "finalize":
                forms_valid = decision_form.is_valid() and forms_valid

            if forms_valid:
                try:
                    with transaction.atomic():
                        draft_report = header_form.save(commit=False)
                        saved_report = save_report_draft(
                            report=draft_report,
                            actor=request.user,
                        )
                        section_formset.save()

                        if action == "finalize":
                            saved_report = transition_report(
                                report=saved_report,
                                target_status=IntelligenceReport.Status.FINAL,
                                actor=request.user,
                                notes=decision_form.cleaned_data["decision_notes"],
                            )
                except ValidationError as exc:
                    header_form.add_error(None, exc)
                else:
                    message = (
                        f"{saved_report.code} berhasil difinalkan dan dikunci."
                        if action == "finalize"
                        else f"Draf {saved_report.code} berhasil disimpan."
                    )
                    messages.success(request, message)
                    return redirect(
                        "dashboard:report-document-edit",
                        report_id=saved_report.pk,
                    )

        elif action in {"distribute", "archive"}:
            if decision_form.is_valid():
                target = {
                    "distribute": IntelligenceReport.Status.DISTRIBUTED,
                    "archive": IntelligenceReport.Status.ARCHIVED,
                }[action]
                try:
                    saved_report = transition_report(
                        report=report,
                        target_status=target,
                        actor=request.user,
                        notes=decision_form.cleaned_data["decision_notes"],
                    )
                except ValidationError as exc:
                    decision_form.add_error(None, exc)
                else:
                    messages.success(
                        request,
                        f"Status {saved_report.code} menjadi {saved_report.get_status_display()}.",
                    )
                    return redirect(
                        "dashboard:report-document-edit",
                        report_id=saved_report.pk,
                    )

    completeness = report_completeness(report)
    return render(
        request,
        "assessments/intelligence_report_edit.html",
        {
            "page_title": report.code or "Laporan Intelijen",
            "active_menu": "reports",
            "report": report,
            "header_form": header_form,
            "section_formset": section_formset,
            "decision_form": decision_form,
            "completeness": completeness,
            "editable": editable,
            "can_contribute": can_contribute,
            "can_approve": can_approve,
        },
    )


def _format_report_date(value) -> str:
    months = (
        "Januari",
        "Februari",
        "Maret",
        "April",
        "Mei",
        "Juni",
        "Juli",
        "Agustus",
        "September",
        "Oktober",
        "November",
        "Desember",
    )
    return f"{value.day} {months[value.month - 1]} {value.year}"


@require_role(*Roles.ALL)
def report_document_export_pdf(request: HttpRequest, report_id) -> HttpResponse:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    report = get_object_or_404(_report_queryset(), pk=report_id)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        leftMargin=2.3 * cm,
        rightMargin=2.3 * cm,
        title=report.hal or report.code or "Laporan Intelijen",
        author=report.dari,
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "MedIntelBody",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=11,
        leading=16,
        alignment=4,
        spaceAfter=7,
    )
    small = ParagraphStyle(
        "MedIntelSmall",
        parent=body,
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#475569"),
    )
    heading = ParagraphStyle(
        "MedIntelHeading",
        parent=body,
        fontName="Times-Bold",
        spaceBefore=10,
        spaceAfter=5,
    )
    centered = ParagraphStyle(
        "MedIntelCentered",
        parent=body,
        fontName="Times-Bold",
        alignment=1,
    )

    story = [
        Paragraph("PRODUK INTELIJEN MEDIK", centered),
        Paragraph(escape(report.code or "KODE BELUM TERSEDIA"), centered),
        Spacer(1, 0.3 * cm),
    ]
    if report.status == IntelligenceReport.Status.DRAFT:
        story.extend(
            [
                Paragraph("DRAF — BELUM DIFINALKAN", centered),
                Spacer(1, 0.2 * cm),
            ]
        )

    header_rows = [
        ("Kepada", report.kepada),
        ("Dari", report.dari),
        ("Tanggal", _format_report_date(report.report_date)),
        ("Tembusan", report.tembusan),
        ("Hal", report.hal),
        ("Nilai", report.nilai),
    ]
    table = Table(
        [
            [
                Paragraph(f"<b>{escape(label)}</b>", body),
                Paragraph(f": {escape(value or '-')}", body),
            ]
            for label, value in header_rows
        ],
        colWidths=[3 * cm, 12.8 * cm],
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    story.extend([table, Spacer(1, 0.4 * cm)])

    sections = list(report.sections.all())
    section_groups = (
        ("I", "INDIKASI", "indikasi_text"),
        ("II", "ANALISIS", "analisis_text"),
        ("III", "DAMPAK", "dampak_text"),
        ("IV", "UPAYA", "upaya_text"),
        ("V", "SARAN TINDAK", "saran_tindak_text"),
    )
    for roman, label, field_name in section_groups:
        story.append(Paragraph(f"{roman}. {label}", heading))
        for section in sections:
            disease_label = (
                f" {escape(section.disease.name)}" if field_name == "indikasi_text" else ""
            )
            story.append(
                Paragraph(f"<b>{section.letter}.{disease_label}</b>", body)
            )
            text_value = escape(getattr(section, field_name) or "-").replace(
                "\n", "<br/>"
            )
            story.append(Paragraph(text_value, body))

    story.extend([Spacer(1, 0.6 * cm), Paragraph(escape(report.signature_block or "-"), body)])

    if sections:
        story.extend([PageBreak(), Paragraph("DAFTAR SUMBER TERBUKA", heading)])
        source_number = 1
        for section in sections:
            for article in section.source_articles.all():
                title = escape(article.title)
                source = escape(article.source.name)
                url = escape(article.normalized_url)
                story.append(
                    Paragraph(
                        f"{source_number}. {source}. {title}.<br/>{url}",
                        small,
                    )
                )
                source_number += 1

    doc.build(story)
    buffer.seek(0)
    filename = (report.code or f"laporan-{report.report_date}").lower()
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    return response
