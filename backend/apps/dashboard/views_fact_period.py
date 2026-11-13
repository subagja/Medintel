"""Review data periode dan angka kematian dengan audit log (02B8N)."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.views.decorators.clickjacking import xframe_options_sameorigin

from apps.accounts.permissions import require_role_by_method
from apps.articles.models import Article
from apps.entities.forms_period import ArticleFactPeriodReviewForm
from apps.entities.models import ArticleFact
from apps.entities.services.review import correct_article_fact


@xframe_options_sameorigin
@require_role_by_method()
@require_http_methods(["GET", "POST"])
def fact_period_review(request: HttpRequest, article_id, fact_id) -> HttpResponse:
    article = get_object_or_404(Article, pk=article_id)
    fact = get_object_or_404(
        ArticleFact.objects.select_related("disease", "location"),
        article=article, pk=fact_id,
    )
    embedded = request.GET.get("embed") == "1"
    detail_url = reverse("dashboard:fact-period-review", kwargs={
        "article_id": article.id, "fact_id": fact_id,
    })
    fields = tuple(ArticleFactPeriodReviewForm.Meta.fields)
    # ModelForm mengisi instance secara in-memory ketika memvalidasi POST.
    # Simpan snapshot sebelum is_valid() agar delta tidak salah dihitung.
    previous = {name: getattr(fact, name) for name in fields}
    if request.method == "POST":
        form = ArticleFactPeriodReviewForm(request.POST, instance=fact)
    else:
        # Alasan yang sudah disimpan harus tampak kembali setelah reload.
        form = ArticleFactPeriodReviewForm(
            instance=fact,
            initial={"correction_reason": fact.validation_notes or ""},
        )

    if request.method == "POST" and form.is_valid():
        changes = {
            name: form.cleaned_data[name]
            for name in fields
            if previous[name] != form.cleaned_data[name]
        }
        notes = form.cleaned_data["correction_reason"].strip()
        with transaction.atomic():
            locked = ArticleFact.objects.select_for_update().get(
                pk=fact_id, article=article,
            )
            if locked.updated_at.isoformat() != request.POST.get("version", ""):
                form.add_error(
                    None,
                    "Belum tersimpan: fakta telah diperbarui sejak halaman dibuka. "
                    "Salin isian penting lalu muat ulang sebelum mencoba lagi.",
                )
            elif not changes and notes == (locked.validation_notes or "").strip():
                messages.info(request, "Tidak ada perubahan yang perlu disimpan.")
                return redirect(detail_url + ("?embed=1&unchanged=1" if embedded else "?unchanged=1"))
            else:
                try:
                    # Alasan saja juga termasuk koreksi yang layak diaudit.
                    correct_article_fact(
                        fact=locked,
                        reviewer=request.user,
                        corrections=changes,
                        notes=notes,
                    )
                except ValidationError as exc:
                    # Tampilkan juga error model pada field yang tidak ada di form.
                    form.add_error(None, "; ".join(exc.messages))
                else:
                    messages.success(
                        request,
                        "Koreksi fakta disimpan. Silakan periksa nilai tersimpan di bawah.",
                    )
                    return redirect(
                        detail_url + ("?embed=1&saved=1" if embedded else "?saved=1")
                    )

    # Jika POST gagal validasi, jangan tampilkan nilai model mutated sebagai saved.
    # Baca ulang status DB; POST yang gagal bisa memutasi instance ModelForm di memori.
    saved_fact = ArticleFact.objects.get(pk=fact_id, article=article)
    return render(request, "dashboard/fact_period_review.html", {
        "saved_fact": saved_fact,
        "page_title": "Tinjau Fakta & Periode",
        "active_menu": "article_validation",
        "article": article,
        "fact": fact,
        "form": form,
        "version": fact.updated_at.isoformat() if request.method == "GET" else request.POST.get("version", ""),
        "save_failed": request.method == "POST",
        "saved_notice": request.GET.get("saved") == "1",
        "unchanged_notice": request.GET.get("unchanged") == "1",
        "embedded": embedded,
        "embed_base": "dashboard/fact_period_embed_base.html" if embedded else "base.html",
    })
