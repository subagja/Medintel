"""Filter artikel yang dapat dipakai bersama oleh beberapa halaman
(Validasi Artikel, Daftar Artikel, dan potensinya halaman lain nanti).

Semua filter dibaca dari querystring GET dan dikembalikan sebagai dua hal:
- queryset `Article` yang sudah difilter
- `dict` konteks untuk merender pilihan dropdown + nilai yang sedang aktif,
  supaya template tidak perlu tahu detail query apapun.
"""
from __future__ import annotations

from django.db.models import Q, QuerySet
from django.utils.dateparse import parse_date

from apps.articles.models import Article
from apps.entities.models import ArticleFact, Disease
from apps.locations.models import Location
from apps.sources.models import Source


DATE_FIELD_CHOICES = (
    ("published_at", "Tanggal Terbit"),
    ("crawled_at", "Tanggal Crawl"),
)


def build_article_filter_options() -> dict:
    """Opsi dropdown untuk filter (dipanggil sekali per request)."""
    return {
        "filter_sources": Source.objects.order_by("name"),
        "filter_diseases": Disease.objects.filter(
            is_active=True,
        ).order_by("name"),
        "filter_locations": (
            Location.objects.filter(
                articles__isnull=False,
            )
            .distinct()
            .order_by("administrative_level", "name")
        ),
        "filter_processing_statuses": Article.ProcessingStatus.choices,
        "filter_trends": ArticleFact.Trend.choices,
        "filter_date_fields": DATE_FIELD_CHOICES,
    }


def apply_article_filters(
    articles: QuerySet,
    params,
) -> tuple[QuerySet, dict]:
    """Terapkan filter dari querystring `params` (biasanya `request.GET`).

    Mengembalikan (queryset_terfilter, selected) -- `selected` berisi nilai
    yang sedang dipilih untuk masing-masing filter, dipakai template supaya
    dropdown menampilkan pilihan yang benar setelah submit.
    """
    selected = {
        "source": params.get("source", "").strip(),
        "disease": params.get("disease", "").strip(),
        "location": params.get("location", "").strip(),
        "processing_status": params.get(
            "processing_status", ""
        ).strip(),
        "trend": params.get("trend", "").strip(),
        "date_field": params.get(
            "date_field", "published_at"
        ).strip(),
        "date_from": params.get("date_from", "").strip(),
        "date_to": params.get("date_to", "").strip(),
    }

    if selected["source"]:
        articles = articles.filter(source__code=selected["source"])

    if selected["disease"]:
        articles = articles.filter(diseases__code=selected["disease"])

    if selected["location"]:
        articles = articles.filter(locations__id=selected["location"])

    if selected["processing_status"]:
        valid_statuses = {
            value for value, _label in Article.ProcessingStatus.choices
        }
        if selected["processing_status"] in valid_statuses:
            articles = articles.filter(
                processing_status=selected["processing_status"],
            )

    if selected["trend"]:
        valid_trends = {
            value for value, _label in ArticleFact.Trend.choices
        }
        if selected["trend"] in valid_trends:
            articles = articles.filter(
                facts__trend=selected["trend"],
            )

    date_field = (
        selected["date_field"]
        if selected["date_field"]
        in {choice for choice, _label in DATE_FIELD_CHOICES}
        else "published_at"
    )
    selected["date_field"] = date_field

    date_from = parse_date(selected["date_from"]) if selected["date_from"] else None
    date_to = parse_date(selected["date_to"]) if selected["date_to"] else None

    if date_from:
        articles = articles.filter(
            **{f"{date_field}__date__gte": date_from},
        )

    if date_to:
        articles = articles.filter(
            **{f"{date_field}__date__lte": date_to},
        )

    # Beberapa filter (diseases, locations, facts) melewati relasi M2M/FK
    # yang bisa menggandakan baris hasil query -- distinct() memastikan
    # satu artikel tetap tampil sekali walau match di banyak baris relasi.
    needs_distinct = any(
        [
            selected["disease"],
            selected["location"],
            selected["trend"],
        ]
    )
    if needs_distinct:
        articles = articles.distinct()

    return articles, selected
