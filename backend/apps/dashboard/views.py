from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from urllib.parse import urlencode
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from apps.sources.models import (
    Source,
    SourceSeedUrl,
    SourceUrlPattern,
)
from apps.sources.forms import (
    SourceForm,
    SourceSeedUrlForm,
    SourceUrlPatternForm,
)
from apps.sources.services import (
    check_source_crawl_readiness,
)
from apps.articles.models import Article
from apps.articles.filters import (
    apply_article_filters,
    build_article_filter_options,
)
from apps.sources.origin import (
    ORIGIN_CHOICES,
    apply_origin_filter,
)
from apps.collection.models import (
    CollectionJob,
    CollectionJobItem,
)
from apps.crawlers.real_crawler import GenericHtmlCrawler
from apps.crawlers.services import run_crawler_in_background
from apps.assessments.forms import (
    ArticleValidationAssessmentForm,
    PrimaryArticleDiseaseForm,
    PrimaryArticleLocationForm,
)
from apps.assessments.models import (
    ArticleValidationAssessment,
    ArticleValidationHistory,
)
from apps.assessments.services.information_balance import (
    recommend_information_balance,
)
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ExtractionReviewLog,
    ValidationStatus,
)
from apps.locations.models import Location
from apps.entities.services.review import (
    validate_article_disease,
    validate_article_fact,
    validate_article_location,
    set_primary_article_disease,
    set_primary_article_location,
)
from apps.indicators.services.generation import (
    generate_indicators_from_fact,
)


def dashboard_overview(request: HttpRequest) -> HttpResponse:
    total_articles = Article.objects.count()

    new_articles = Article.objects.filter(
        processing_status=Article.ProcessingStatus.NEW,
    ).count()

    processed_articles = Article.objects.filter(
        processing_status=Article.ProcessingStatus.PROCESSED,
    ).count()

    validated_articles = Article.objects.filter(
        processing_status=Article.ProcessingStatus.VALIDATED,
    ).count()

    rejected_articles = Article.objects.filter(
        processing_status=Article.ProcessingStatus.REJECTED,
    ).count()

    latest_articles = (
        Article.objects
        .select_related("source")
        .prefetch_related("diseases", "locations")
        .order_by("-crawled_at")[:10]
    )

    context = {
        "page_title": "Dashboard Ringkasan",
        "active_menu": "dashboard",
        "summary": {
            "total_articles": total_articles,
            "new_articles": new_articles,
            "processed_articles": processed_articles,
            "validated_articles": validated_articles,
            "rejected_articles": rejected_articles,
        },
        "latest_articles": latest_articles,
    }

    return render(
        request,
        "dashboard/index.html",
        context,
    )


def _ready_html_sources() -> list[Source]:
    sources = (
        _source_readiness_queryset()
        .filter(
            crawl_strategy=Source.CrawlStrategy.HTML,
        )
        .order_by("name")
    )

    ready_sources = []

    for source in sources:
        _attach_source_readiness(source)

        if source.crawl_readiness.is_ready:
            ready_sources.append(source)

    return ready_sources


def crawler_list(request: HttpRequest) -> HttpResponse:
    source_code = request.GET.get(
        "source",
        "",
    ).strip()
    status = request.GET.get(
        "status",
        "",
    ).strip()

    jobs = (
        CollectionJob.objects
        .filter(
            job_type=CollectionJob.JobType.CRAWLER,
        )
        .select_related(
            "source",
            "triggered_by",
        )
        .annotate(
            item_count=Count("items"),
        )
        .order_by("-created_at")
    )

    if source_code:
        jobs = jobs.filter(
            source__code=source_code,
        )

    valid_statuses = {
        value
        for value, _label
        in CollectionJob.Status.choices
    }

    if status in valid_statuses:
        jobs = jobs.filter(status=status)

    all_jobs = CollectionJob.objects.filter(
        job_type=CollectionJob.JobType.CRAWLER,
    )
    totals = all_jobs.aggregate(
        total_created=Sum("total_created"),
        total_rejected=Sum("total_rejected"),
        total_failed=Sum("total_failed"),
    )

    paginator = Paginator(jobs, 25)
    page_obj = paginator.get_page(
        request.GET.get("page")
    )

    ready_sources = _ready_html_sources()
    ready_indonesia_count = sum(
        1
        for source in ready_sources
        if source.source_type
        != Source.SourceType.INTERNATIONAL_MEDIA
    )

    context = {
        "page_title": "Crawler Artikel/Web",
        "active_menu": "crawler-artikel",
        "ready_sources": ready_sources,
        "ready_indonesia_count": ready_indonesia_count,
        "filter_sources": Source.objects.order_by("name"),
        "job_statuses": CollectionJob.Status.choices,
        "selected_source": source_code,
        "selected_status": status,
        "page_obj": page_obj,
        "summary": {
            "total_jobs": all_jobs.count(),
            "running_jobs": all_jobs.filter(
                status=CollectionJob.Status.RUNNING,
            ).count(),
            "total_created": totals["total_created"] or 0,
            "total_rejected": totals["total_rejected"] or 0,
            "total_failed": totals["total_failed"] or 0,
        },
    }

    return render(
        request,
        "dashboard/crawler_list.html",
        context,
    )


def _parse_optional_positive_int(
    value: str,
    *,
    label: str,
    maximum: int = 1000,
) -> int | None:
    value = value.strip()

    if not value:
        return None

    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValidationError(
            f"{label} harus berupa angka."
        ) from exc

    if parsed < 1 or parsed > maximum:
        raise ValidationError(
            f"{label} harus bernilai 1–{maximum}."
        )

    return parsed


@require_POST
def crawler_run(request: HttpRequest) -> HttpResponse:
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )
    source_code = request.POST.get(
        "source",
        "",
    ).strip()

    try:
        limit = _parse_optional_positive_int(
            request.POST.get("limit", ""),
            label="Batas artikel",
        )
        candidate_limit = _parse_optional_positive_int(
            request.POST.get("candidate_limit", ""),
            label="Batas kandidat",
        )
    except ValidationError as exc:
        if is_ajax:
            return JsonResponse(
                {"error": exc.messages[0]},
                status=400,
            )
        messages.error(
            request,
            exc.messages[0],
        )
        return redirect("dashboard:crawler-list")

    ready_sources = _ready_html_sources()

    if source_code == "all":
        selected_sources = ready_sources
    elif source_code == "all_indonesia":
        selected_sources = [
            source
            for source in ready_sources
            if source.source_type
            != Source.SourceType.INTERNATIONAL_MEDIA
        ]
    else:
        selected_sources = [
            source
            for source in ready_sources
            if source.code == source_code
        ]

    if not source_code:
        if is_ajax:
            return JsonResponse(
                {"error": "Pilih sumber yang akan dijalankan."},
                status=400,
            )
        messages.error(
            request,
            "Pilih sumber yang akan dijalankan.",
        )
        return redirect("dashboard:crawler-list")

    if not selected_sources:
        error_text = (
            "Sumber tidak ditemukan atau belum memenuhi "
            "status Siap Crawling."
        )
        if is_ajax:
            return JsonResponse(
                {"error": error_text},
                status=400,
            )
        messages.error(
            request,
            error_text,
        )
        return redirect("dashboard:crawler-list")

    triggered_by = (
        request.user
        if request.user.is_authenticated
        else None
    )
    started_sources = []
    skipped_sources = []
    request_started_at = timezone.now()

    for source in selected_sources:
        already_running = CollectionJob.objects.filter(
            source=source,
            job_type=CollectionJob.JobType.CRAWLER,
            status=CollectionJob.Status.RUNNING,
        ).exists()

        if already_running:
            skipped_sources.append(source.code)
            continue

        crawler = GenericHtmlCrawler(
            source_code=source.code,
            limit=limit,
            candidate_limit=candidate_limit,
        )

        # Crawl berjalan di background thread supaya request ini tidak
        # perlu menunggu proses selesai. Progres tetap terlihat karena
        # CollectionJob langsung dibuat berstatus "Sedang Berjalan" dan
        # tabel di halaman crawler-list akan mem-polling perubahannya.
        run_crawler_in_background(
            crawler,
            triggered_by=triggered_by,
            trigger_type="user",
        )
        started_sources.append(source.code)

    if is_ajax:
        return JsonResponse(
            {
                "started": started_sources,
                "skipped": skipped_sources,
                # ISO 8601 dengan offset UTC eksplisit, dipakai client
                # untuk polling "job apa saja yang baru muncul sejak
                # request ini dikirim" lewat crawler-status.
                "since": request_started_at.isoformat(),
            }
        )

    if started_sources:
        messages.success(
            request,
            (
                f"Crawler dimulai untuk {len(started_sources)} sumber: "
                + ", ".join(started_sources)
                + ". Status akan diperbarui otomatis pada tabel di bawah."
            ),
        )

    if skipped_sources:
        messages.warning(
            request,
            (
                "Dilewati karena masih berjalan: "
                + ", ".join(skipped_sources)
                + "."
            ),
        )

    return redirect("dashboard:crawler-list")


def _serialize_job_status(job: CollectionJob) -> dict:
    status_labels = dict(CollectionJob.Status.choices)

    return {
        "id": str(job.id),
        "source_code": job.source.code,
        "status": job.status,
        "status_label": status_labels.get(
            job.status,
            job.status,
        ),
        "started_at": (
            timezone.localtime(job.started_at).strftime(
                "%d %b %Y %H:%M"
            )
            if job.started_at
            else None
        ),
        "total_found": job.total_found,
        "total_created": job.total_created,
        "total_duplicate": job.total_duplicate,
        "total_rejected": job.total_rejected,
        "total_failed": job.total_failed,
        "is_finished": job.status
        not in (
            CollectionJob.Status.PENDING,
            CollectionJob.Status.RUNNING,
        ),
    }


def crawler_status(request: HttpRequest) -> HttpResponse:
    """Endpoint JSON untuk polling status job crawler dari JavaScript.

    Dua mode pemakaian:
    - `job_ids` (dipisah koma): job yang sudah tampil di tabel, dicek
      progresnya (dipakai polling rutin).
    - `source_codes` (dipisah koma) + `since` (ISO datetime): dipakai
      begitu crawler baru saja dimulai lewat AJAX, untuk menemukan
      CollectionJob yang baru terbentuk di background thread sebelum
      client tahu UUID job-nya.
    """
    raw_ids = request.GET.get("job_ids", "")
    job_ids = [
        value.strip()
        for value in raw_ids.split(",")
        if value.strip()
    ]

    if job_ids:
        jobs = CollectionJob.objects.filter(
            id__in=job_ids,
        ).select_related("source")

        return JsonResponse(
            {"jobs": [_serialize_job_status(job) for job in jobs]}
        )

    raw_codes = request.GET.get("source_codes", "")
    source_codes = [
        value.strip()
        for value in raw_codes.split(",")
        if value.strip()
    ]

    since_raw = request.GET.get("since", "")
    since = parse_datetime(since_raw) if since_raw else None

    if since and timezone.is_naive(since):
        since = timezone.make_aware(since, timezone.utc)

    if not source_codes or since is None:
        return JsonResponse({"jobs": []})

    found_jobs = []

    for code in source_codes:
        job = (
            CollectionJob.objects.filter(
                source__code=code,
                job_type=CollectionJob.JobType.CRAWLER,
                created_at__gte=since,
            )
            .select_related("source")
            .order_by("-created_at")
            .first()
        )

        if job:
            found_jobs.append(job)

    return JsonResponse(
        {"jobs": [_serialize_job_status(job) for job in found_jobs]}
    )


def crawler_job_detail(
    request: HttpRequest,
    job_id,
) -> HttpResponse:
    job = get_object_or_404(
        CollectionJob.objects.select_related(
            "source",
            "triggered_by",
        ),
        id=job_id,
        job_type=CollectionJob.JobType.CRAWLER,
    )

    status = request.GET.get(
        "status",
        "",
    ).strip()
    search_query = request.GET.get(
        "q",
        "",
    ).strip()

    items = (
        job.items
        .select_related("article")
        .order_by("created_at")
    )

    valid_statuses = {
        value
        for value, _label
        in CollectionJobItem.Status.choices
    }

    if status in valid_statuses:
        items = items.filter(status=status)

    if search_query:
        items = items.filter(
            Q(title__icontains=search_query)
            | Q(original_url__icontains=search_query)
            | Q(reason__icontains=search_query)
            | Q(error_message__icontains=search_query)
        )

    paginator = Paginator(items, 50)
    page_obj = paginator.get_page(
        request.GET.get("page")
    )

    duration_seconds = None

    if job.started_at:
        ended_at = job.finished_at or timezone.now()
        duration_seconds = max(
            int(
                (
                    ended_at - job.started_at
                ).total_seconds()
            ),
            0,
        )

    context = {
        "page_title": "Detail Proses Crawler",
        "active_menu": "crawler-artikel",
        "job": job,
        "page_obj": page_obj,
        "item_statuses": CollectionJobItem.Status.choices,
        "selected_status": status,
        "search_query": search_query,
        "duration_seconds": duration_seconds,
        "duration_label": (
            f"{duration_seconds} detik"
            if duration_seconds is not None
            else "-"
        ),
    }

    return render(
        request,
        "dashboard/crawler_job_detail.html",
        context,
    )


def _source_readiness_queryset():
    return Source.objects.annotate(
        active_seed_count=Count(
            "seed_urls",
            filter=Q(
                seed_urls__is_active=True,
            ),
            distinct=True,
        ),
        active_pattern_count=Count(
            "url_patterns",
            filter=Q(
                url_patterns__is_active=True,
            ),
            distinct=True,
        ),
        active_allow_pattern_count=Count(
            "url_patterns",
            filter=Q(
                url_patterns__is_active=True,
                url_patterns__pattern_type=(
                    SourceUrlPattern.PatternType.ALLOW
                ),
            ),
            distinct=True,
        ),
        active_deny_pattern_count=Count(
            "url_patterns",
            filter=Q(
                url_patterns__is_active=True,
                url_patterns__pattern_type=(
                    SourceUrlPattern.PatternType.DENY
                ),
            ),
            distinct=True,
        ),
    )


def _attach_source_readiness(source: Source) -> Source:
    source.crawl_readiness = (
        check_source_crawl_readiness(source)
    )

    return source


def source_list(request: HttpRequest) -> HttpResponse:
    search_query = request.GET.get(
        "q",
        "",
    ).strip()

    source_type = request.GET.get(
        "source_type",
        "",
    ).strip()

    origin = request.GET.get(
        "origin",
        "",
    ).strip()

    status = request.GET.get(
        "status",
        "",
    ).strip()

    sources = _source_readiness_queryset().order_by(
        "name"
    )

    if search_query:
        sources = sources.filter(
            Q(name__icontains=search_query)
            | Q(code__icontains=search_query)
            | Q(domain__icontains=search_query)
        )

    if source_type:
        sources = sources.filter(
            source_type=source_type,
        )

    sources = apply_origin_filter(sources, origin)

    if status == "active":
        sources = sources.filter(
            is_active=True,
        )
    elif status == "verified":
        sources = sources.filter(
            is_verified=True,
        )
    elif status == "inactive":
        sources = sources.filter(
            is_active=False,
        )

    source_rows = [
        _attach_source_readiness(source)
        for source in sources
    ]

    if status == "crawl_ready":
        source_rows = [
            source
            for source in source_rows
            if source.crawl_readiness.is_ready
        ]
    elif status == "not_ready":
        source_rows = [
            source
            for source in source_rows
            if not source.crawl_readiness.is_ready
        ]

    all_sources = [
        _attach_source_readiness(source)
        for source in _source_readiness_queryset()
    ]

    summary = {
        "total": len(all_sources),
        "active": sum(
            source.is_active
            for source in all_sources
        ),
        "verified": sum(
            source.is_verified
            for source in all_sources
        ),
        "crawl_ready": sum(
            source.crawl_readiness.is_ready
            for source in all_sources
        ),
        "indonesia": sum(
            source.source_type
            != Source.SourceType.INTERNATIONAL_MEDIA
            for source in all_sources
        ),
        "indonesia_crawl_ready": sum(
            source.crawl_readiness.is_ready
            and source.source_type
            != Source.SourceType.INTERNATIONAL_MEDIA
            for source in all_sources
        ),
    }

    context = {
        "page_title": "Sumber OSINT",
        "active_menu": "sources",
        "sources": source_rows,
        "source_types": Source.SourceType.choices,
        "origin_choices": ORIGIN_CHOICES,
        "search_query": search_query,
        "selected_source_type": source_type,
        "selected_origin": origin,
        "selected_status": status,
        "summary": summary,
    }

    return render(
        request,
        "dashboard/source_list.html",
        context,
    )


def _validation_queryset():
    disease_exists = ArticleDisease.objects.filter(
        article_id=OuterRef("pk"),
    )

    location_exists = ArticleLocation.objects.filter(
        article_id=OuterRef("pk"),
    )

    numeric_fact_exists = ArticleFact.objects.filter(
        article_id=OuterRef("pk"),
    ).filter(
        Q(case_count__isnull=False)
        | Q(death_count__isnull=False)
    )

    return (
        Article.objects
        .select_related(
            "source",
            "validation_assessment",
        )
        .prefetch_related(
            "diseases",
            "locations",
        )
        .annotate(
            has_extracted_disease=Exists(
                disease_exists
            ),
            has_extracted_location=Exists(
                location_exists
            ),
            has_numeric_fact=Exists(
                numeric_fact_exists
            ),
        )
        .exclude(
            processing_status=(
                Article.ProcessingStatus.FAILED
            ),
        )
        .order_by(
            "-published_at",
            "-crawled_at",
        )
    )


def _extra_filter_querystring(filters: dict) -> str:
    """Querystring (diawali '&') dari filter lanjutan yang sedang aktif,
    dipakai template supaya link eligibility/pagination tidak me-reset
    filter lanjutan yang sudah dipilih pengguna.
    """
    params = {
        key: value
        for key, value in filters.items()
        if value and key != "date_field"
    }
    if filters.get("date_field") and filters.get(
        "date_field"
    ) != "published_at":
        params["date_field"] = filters["date_field"]

    if not params:
        return ""

    return "&" + urlencode(params)


def _build_validation_redirect_url(
    *,
    article_id,
    tab: str,
    eligibility: str,
    filters: dict,
) -> str:
    """URL kembali ke workspace Validasi Artikel setelah aksi POST,
    dengan seluruh filter (eligibility + filter artikel baru) tetap
    dipertahankan supaya daftar artikel tidak ter-reset ke tanpa filter.
    """
    params = {
        "article": str(article_id),
        "eligibility": eligibility,
        "tab": tab,
    }

    for key in (
        "source",
        "disease",
        "location",
        "processing_status",
        "trend",
        "date_field",
        "date_from",
        "date_to",
    ):
        value = filters.get(key)
        if value:
            params[key] = value

    query = urlencode(params)

    return f"{reverse('dashboard:article-validation')}?{query}"


def article_validation(request: HttpRequest) -> HttpResponse:
    articles = _validation_queryset()

    eligibility_filter = request.GET.get(
        "eligibility",
        "all",
    )

    if eligibility_filter == "eligible":
        articles = articles.filter(
            has_extracted_disease=True,
            has_extracted_location=True,
            has_numeric_fact=True,
        )
    elif eligibility_filter == "needs_review":
        articles = articles.filter(
            Q(has_extracted_disease=False)
            | Q(has_extracted_location=False)
            | Q(has_numeric_fact=False)
        )

    articles, active_filters = apply_article_filters(
        articles,
        request.GET,
    )

    selected_article_id = (
        request.POST.get("article_id")
        or request.GET.get("article")
    )

    if selected_article_id:
        selected_article = get_object_or_404(
            articles,
            id=selected_article_id,
        )
    else:
        selected_article = articles.first()

    assessment = None
    form = None
    primary_disease_form = None
    primary_location_form = None
    assessment_history = []
    disease_history = []
    location_history = []
    information_balance_recommendation = None
    selected_diseases = []
    selected_locations = []
    selected_facts = []
    selected_eligibility = None
    requested_validation_tab = (
        request.POST.get("active_tab")
        or request.GET.get("tab")
        or "validation"
    )
    allowed_validation_tabs = {
        "validation",
        "disease",
        "location",
        "information",
        "history",
    }
    active_validation_tab = (
        requested_validation_tab
        if requested_validation_tab in allowed_validation_tabs
        else "validation"
    )

    if selected_article is not None:
        selected_diseases = list(
            ArticleDisease.objects.filter(
                article=selected_article,
            )
            .select_related("disease")
            .order_by(
                "-is_primary",
                "-confidence_score",
            )
        )

        selected_locations = list(
            ArticleLocation.objects.filter(
                article=selected_article,
            )
            .select_related("location")
            .order_by(
                "-is_primary",
                "-confidence_score",
            )
        )

        selected_facts = list(
            ArticleFact.objects.filter(
                article=selected_article,
            )
            .select_related(
                "disease",
                "location",
            )
            .order_by(
                "-confidence_score",
                "-created_at",
            )
        )

        selected_eligibility = {
            "is_eligible": bool(
                selected_article.has_extracted_disease
                and selected_article.has_extracted_location
                and selected_article.has_numeric_fact
            ),
            "has_disease": (
                selected_article.has_extracted_disease
            ),
            "has_location": (
                selected_article.has_extracted_location
            ),
            "has_numeric_fact": (
                selected_article.has_numeric_fact
            ),
        }

        assessment, _ = (
            ArticleValidationAssessment.objects.get_or_create(
                article=selected_article,
                defaults={
                    "validation_status": (
                        ArticleValidationAssessment
                        .ValidationStatus
                        .PENDING
                    ),
                    "source_reliability": (
                        ArticleValidationAssessment
                        .SourceReliability
                        .F
                    ),
                    "information_credibility": (
                        ArticleValidationAssessment
                        .InformationCredibility
                        .UNASSESSABLE
                    ),
                },
            )
        )

        information_balance_recommendation = (
            recommend_information_balance(selected_article)
        )

        post_action = request.POST.get(
            "action",
            "save_assessment",
        )

        if (
            request.method == "POST"
            and post_action == "correct_primary_disease"
        ):
            active_validation_tab = "disease"
            form = ArticleValidationAssessmentForm(
                instance=assessment,
            )
            primary_disease_form = PrimaryArticleDiseaseForm(
                request.POST,
                article=selected_article,
            )
            primary_location_form = PrimaryArticleLocationForm(
                article=selected_article,
            )

            if not request.user.is_authenticated:
                primary_disease_form.add_error(
                    None,
                    (
                        "Pengguna harus login untuk menetapkan "
                        "penyakit utama."
                    ),
                )
            elif primary_disease_form.is_valid():
                try:
                    correction_result = (
                        set_primary_article_disease(
                            article=selected_article,
                            disease=(
                                primary_disease_form
                                .cleaned_data["primary_disease"]
                            ),
                            reviewer=request.user,
                            notes=(
                                primary_disease_form
                                .cleaned_data[
                                    "disease_correction_notes"
                                ]
                            ),
                        )
                    )
                except ValidationError as exc:
                    primary_disease_form.add_error(
                        None,
                        exc,
                    )
                else:
                    if correction_result.changed:
                        messages.success(
                            request,
                            (
                                "Penyakit utama ditetapkan menjadi "
                                f"{correction_result.relation.disease}. "
                                f"{correction_result.context_count} "
                                "penyakit lain tetap disimpan sebagai "
                                "konteks."
                            ),
                        )
                    else:
                        messages.info(
                            request,
                            (
                                "Penyakit utama tidak berubah; "
                                "riwayat baru tidak dibuat."
                            ),
                        )

                    return redirect(
                        _build_validation_redirect_url(
                            article_id=selected_article.id,
                            tab="disease",
                            eligibility=eligibility_filter,
                            filters=active_filters,
                        )
                    )
        elif (
            request.method == "POST"
            and post_action == "correct_primary_location"
        ):
            active_validation_tab = "location"
            form = ArticleValidationAssessmentForm(
                instance=assessment,
            )
            primary_disease_form = PrimaryArticleDiseaseForm(
                article=selected_article,
            )
            primary_location_form = PrimaryArticleLocationForm(
                request.POST,
                article=selected_article,
            )

            if not request.user.is_authenticated:
                primary_location_form.add_error(
                    None,
                    (
                        "Pengguna harus login untuk mengoreksi "
                        "lokasi utama."
                    ),
                )
            elif primary_location_form.is_valid():
                try:
                    correction_result = (
                        set_primary_article_location(
                            article=selected_article,
                            location=(
                                primary_location_form
                                .cleaned_data["primary_location"]
                            ),
                            reviewer=request.user,
                            notes=(
                                primary_location_form
                                .cleaned_data["correction_notes"]
                            ),
                        )
                    )
                except ValidationError as exc:
                    primary_location_form.add_error(
                        None,
                        exc,
                    )
                else:
                    if correction_result.changed:
                        messages.success(
                            request,
                            (
                                "Lokasi kejadian utama ditetapkan "
                                f"menjadi "
                                f"{correction_result.relation.location}. "
                                f"{correction_result.context_count} "
                                "lokasi lain tetap disimpan sebagai "
                                "konteks."
                            ),
                        )
                    else:
                        messages.info(
                            request,
                            (
                                "Lokasi utama tidak berubah; "
                                "riwayat baru tidak dibuat."
                            ),
                        )

                    return redirect(
                        _build_validation_redirect_url(
                            article_id=selected_article.id,
                            tab="location",
                            eligibility=eligibility_filter,
                            filters=active_filters,
                        )
                    )
        else:
            primary_disease_form = PrimaryArticleDiseaseForm(
                article=selected_article,
            )
            primary_location_form = PrimaryArticleLocationForm(
                article=selected_article,
            )

            if request.method == "POST":
                previous_values = {
                    "status": assessment.validation_status,
                    "source_reliability": (
                        assessment.source_reliability
                    ),
                    "information_credibility": (
                        assessment.information_credibility
                    ),
                    "assessment_notes": assessment.assessment_notes,
                    "relevance_notes": assessment.relevance_notes,
                }

                form = ArticleValidationAssessmentForm(
                    request.POST,
                    instance=assessment,
                )

                if form.is_valid():
                    with transaction.atomic():
                        saved_assessment = form.save(
                            commit=False
                        )

                        if request.user.is_authenticated:
                            saved_assessment.evaluated_by = (
                                request.user
                            )

                        saved_assessment.save()

                        has_changed = _assessment_has_changed(
                            previous_values=previous_values,
                            assessment=saved_assessment,
                        )

                        if has_changed:
                            ArticleValidationHistory.objects.create(
                                assessment=saved_assessment,
                                previous_status=(
                                    previous_values["status"]
                                ),
                                new_status=(
                                    saved_assessment.validation_status
                                ),
                                previous_source_reliability=(
                                    previous_values[
                                        "source_reliability"
                                    ]
                                ),
                                new_source_reliability=(
                                    saved_assessment.source_reliability
                                ),
                                previous_information_credibility=(
                                    previous_values[
                                        "information_credibility"
                                    ]
                                ),
                                new_information_credibility=(
                                    saved_assessment
                                    .information_credibility
                                ),
                                change_notes=(
                                    saved_assessment.assessment_notes
                                ),
                                changed_by=(
                                    request.user
                                    if request.user.is_authenticated
                                    else None
                                ),
                            )

                        generation_summary = None

                        if (
                            saved_assessment.validation_status
                            == ArticleValidationAssessment
                            .ValidationStatus
                            .VALIDATED
                        ):
                            if not request.user.is_authenticated:
                                raise ValidationError(
                                    "Pengguna harus login untuk "
                                    "memvalidasi artikel."
                                )

                            generation_summary = (
                                _validate_extractions_and_generate_indicators(
                                    article=selected_article,
                                    reviewer=request.user,
                                    notes=(
                                        saved_assessment.assessment_notes
                                        or saved_assessment.relevance_notes
                                    ),
                                )
                            )

                        _synchronize_article_status(
                            article=selected_article,
                            assessment=saved_assessment,
                        )

                    messages.success(
                        request,
                        (
                            "Validasi artikel berhasil disimpan "
                            f"dengan nilai "
                            f"{saved_assessment.admiralty_code}."
                        ),
                    )

                    if generation_summary is not None:
                        messages.info(
                            request,
                            (
                                "Hasil ekstraksi tervalidasi: "
                                f"{generation_summary['diseases_validated']} "
                                "penyakit, "
                                f"{generation_summary['locations_validated']} "
                                "lokasi, dan "
                                f"{generation_summary['facts_validated']} "
                                "fakta. "
                                "Indikator baru: "
                                f"{generation_summary['indicators_created']}; "
                                "indikator yang sudah ada: "
                                f"{generation_summary['indicators_existing']}; "
                                "fakta tanpa kandidat indikator: "
                                f"{generation_summary['facts_skipped']}."
                            ),
                        )

                    return redirect(
                        _build_validation_redirect_url(
                            article_id=selected_article.id,
                            tab=active_validation_tab,
                            eligibility=eligibility_filter,
                            filters=active_filters,
                        )
                    )
            else:
                form = ArticleValidationAssessmentForm(
                    instance=assessment,
                )

        assessment_history = (
            assessment.history
            .select_related("changed_by")
            .all()
        )

        location_history = _location_correction_history(
            selected_locations
        )

        disease_history = _disease_correction_history(
            selected_diseases
        )

    base_articles = _validation_queryset()
    total_articles = base_articles.count()

    validated_count = base_articles.filter(
        validation_assessment__validation_status=(
            ArticleValidationAssessment
            .ValidationStatus
            .VALIDATED
        )
    ).count()

    rejected_count = base_articles.filter(
        validation_assessment__validation_status=(
            ArticleValidationAssessment
            .ValidationStatus
            .REJECTED
        )
    ).count()

    pending_count = (
        total_articles
        - validated_count
        - rejected_count
    )

    eligible_count = _validation_queryset().filter(
        has_extracted_disease=True,
        has_extracted_location=True,
        has_numeric_fact=True,
    ).count()

    needs_review_count = (
        _validation_queryset()
        .filter(
            Q(has_extracted_disease=False)
            | Q(has_extracted_location=False)
            | Q(has_numeric_fact=False)
        )
        .count()
    )

    context = {
        "page_title": "Validasi Artikel",
        "active_menu": "article_validation",
        "articles": articles[:50],
        "selected_article": selected_article,
        "assessment": assessment,
        "assessment_form": form,
        "primary_disease_form": primary_disease_form,
        "primary_location_form": primary_location_form,
        "assessment_history": assessment_history,
        "disease_history": disease_history,
        "location_history": location_history,
        "information_balance_recommendation": (
            information_balance_recommendation
        ),
        "active_validation_tab": active_validation_tab,
        "selected_diseases": selected_diseases,
        "selected_locations": selected_locations,
        "selected_facts": selected_facts,
        "selected_eligibility": selected_eligibility,
        "eligibility_filter": eligibility_filter,
        "active_filters": active_filters,
        "extra_filter_qs": _extra_filter_querystring(active_filters),
        **build_article_filter_options(),
        "summary": {
            "total": total_articles,
            "pending": pending_count,
            "validated": validated_count,
            "rejected": rejected_count,
            "eligible": eligible_count,
            "needs_review": needs_review_count,
        },
    }

    return render(
        request,
        "dashboard/article_validation.html",
        context,
    )


def article_list(request: HttpRequest) -> HttpResponse:
    """Halaman "Daftar Artikel": daftar seluruh artikel hasil crawling
    dengan filter lengkap (sumber, penyakit, lokasi, tanggal, status
    pemrosesan, tren kasus), terpisah dari workspace Validasi Artikel
    yang berfokus pada aksi validasi satu per satu.
    """
    articles = (
        Article.objects.select_related(
            "source",
            "validation_assessment",
        )
        .prefetch_related(
            "diseases",
            "locations",
        )
        .order_by(
            "-published_at",
            "-crawled_at",
        )
    )

    articles, active_filters = apply_article_filters(
        articles,
        request.GET,
    )

    paginator = Paginator(articles, 25)
    page_obj = paginator.get_page(
        request.GET.get("page")
    )

    context = {
        "page_title": "Daftar Artikel",
        "active_menu": "article_list",
        "page_obj": page_obj,
        "active_filters": active_filters,
        "extra_filter_qs": _extra_filter_querystring(active_filters),
        **build_article_filter_options(),
    }

    return render(
        request,
        "dashboard/article_list.html",
        context,
    )


def _disease_correction_history(
    disease_relations: list[ArticleDisease],
) -> list[ExtractionReviewLog]:
    relation_ids = [
        relation.id
        for relation in disease_relations
    ]

    if not relation_ids:
        return []

    review_logs = list(
        ExtractionReviewLog.objects.filter(
            object_type=(
                ExtractionReviewLog
                .ObjectType
                .ARTICLE_DISEASE
            ),
            object_id__in=relation_ids,
            action=ExtractionReviewLog.Action.CORRECT,
        )
        .select_related("reviewer")
        .order_by("-reviewed_at")
    )

    primary_logs = [
        review_log
        for review_log in review_logs
        if (review_log.after_data or {}).get("is_primary")
    ]

    disease_ids = {
        (review_log.after_data or {}).get("disease_id")
        for review_log in primary_logs
        if (review_log.after_data or {}).get("disease_id")
    }

    diseases = {
        str(disease_id): disease
        for disease_id, disease in (
            Disease.objects.in_bulk(disease_ids).items()
        )
    }

    for review_log in primary_logs:
        disease_id = (
            review_log.after_data or {}
        ).get("disease_id")
        disease = diseases.get(str(disease_id))
        review_log.primary_disease_label = (
            str(disease)
            if disease is not None
            else "Penyakit tidak tersedia"
        )

    return primary_logs


def _location_correction_history(
    location_relations: list[ArticleLocation],
) -> list[ExtractionReviewLog]:
    relation_ids = [
        relation.id
        for relation in location_relations
    ]

    if not relation_ids:
        return []

    review_logs = list(
        ExtractionReviewLog.objects.filter(
            object_type=(
                ExtractionReviewLog
                .ObjectType
                .ARTICLE_LOCATION
            ),
            object_id__in=relation_ids,
            action=ExtractionReviewLog.Action.CORRECT,
        )
        .select_related("reviewer")
        .order_by("-reviewed_at")
    )

    primary_logs = [
        review_log
        for review_log in review_logs
        if (review_log.after_data or {}).get("is_primary")
    ]

    location_ids = {
        (review_log.after_data or {}).get("location_id")
        for review_log in primary_logs
        if (review_log.after_data or {}).get("location_id")
    }

    locations = {
        str(location_id): location
        for location_id, location in (
            Location.objects.in_bulk(location_ids).items()
        )
    }

    for review_log in primary_logs:
        location_id = (
            review_log.after_data or {}
        ).get("location_id")
        location = locations.get(str(location_id))
        review_log.primary_location_label = (
            str(location)
            if location is not None
            else "Lokasi tidak tersedia"
        )

    return primary_logs


def _validate_extractions_and_generate_indicators(
    *,
    article: Article,
    reviewer,
    notes: str = "",
) -> dict:
    disease_relations = list(
        ArticleDisease.objects.filter(
            article=article,
        ).select_related("disease")
    )

    location_relations = list(
        ArticleLocation.objects.filter(
            article=article,
        ).select_related("location")
    )

    facts = list(
        ArticleFact.objects.filter(
            article=article,
        ).select_related(
            "disease",
            "location",
        )
    )

    if not disease_relations:
        raise ValidationError(
            "Artikel belum memiliki hasil ekstraksi penyakit."
        )

    if not location_relations:
        raise ValidationError(
            "Artikel belum memiliki hasil ekstraksi lokasi."
        )

    if not any(
        fact.case_count is not None
        or fact.death_count is not None
        for fact in facts
    ):
        raise ValidationError(
            "Artikel belum memiliki fakta numerik berupa "
            "jumlah kasus atau kematian."
        )

    summary = {
        "diseases_validated": 0,
        "locations_validated": 0,
        "facts_validated": 0,
        "indicators_created": 0,
        "indicators_existing": 0,
        "facts_skipped": 0,
    }

    for relation in disease_relations:
        if relation.validation_status == ValidationStatus.UNREVIEWED:
            validate_article_disease(
                relation=relation,
                reviewer=reviewer,
                notes=notes,
            )
            summary["diseases_validated"] += 1

    for relation in location_relations:
        if relation.validation_status == ValidationStatus.UNREVIEWED:
            validate_article_location(
                relation=relation,
                reviewer=reviewer,
                notes=notes,
            )
            summary["locations_validated"] += 1

    eligible_facts = []

    for fact in facts:
        if fact.validation_status == ValidationStatus.REJECTED:
            continue

        if not fact.disease_id or not fact.location_id:
            continue

        if fact.validation_status == ValidationStatus.UNREVIEWED:
            validate_article_fact(
                fact=fact,
                reviewer=reviewer,
                notes=notes,
            )
            summary["facts_validated"] += 1

        fact.refresh_from_db()

        if fact.validation_status in {
            ValidationStatus.VALIDATED,
            ValidationStatus.CORRECTED,
        }:
            eligible_facts.append(fact)

    if not eligible_facts:
        raise ValidationError(
            "Tidak ada fakta lengkap yang dapat digunakan untuk "
            "membentuk indikator. Pastikan fakta memiliki penyakit "
            "dan lokasi."
        )

    for fact in eligible_facts:
        result = generate_indicators_from_fact(fact)
        summary["indicators_created"] += len(
            result.indicators_created
        )
        summary["indicators_existing"] += len(
            result.indicators_existing
        )

        if result.skipped_reason:
            summary["facts_skipped"] += 1

    return summary


def _assessment_has_changed(
    previous_values: dict,
    assessment: ArticleValidationAssessment,
) -> bool:
    return any(
        [
            (
                previous_values["status"]
                != assessment.validation_status
            ),
            (
                previous_values["source_reliability"]
                != assessment.source_reliability
            ),
            (
                previous_values["information_credibility"]
                != assessment.information_credibility
            ),
            (
                previous_values["assessment_notes"]
                != assessment.assessment_notes
            ),
            (
                previous_values["relevance_notes"]
                != assessment.relevance_notes
            ),
        ]
    )


def _synchronize_article_status(
    article: Article,
    assessment: ArticleValidationAssessment,
) -> None:
    status_mapping = {
        (
            ArticleValidationAssessment
            .ValidationStatus
            .PENDING
        ): Article.ProcessingStatus.PROCESSED,
        (
            ArticleValidationAssessment
            .ValidationStatus
            .VALIDATED
        ): Article.ProcessingStatus.VALIDATED,
        (
            ArticleValidationAssessment
            .ValidationStatus
            .REJECTED
        ): Article.ProcessingStatus.REJECTED,
    }

    new_processing_status = status_mapping[
        assessment.validation_status
    ]

    new_rejection_reason = ""

    if (
        assessment.validation_status
        == ArticleValidationAssessment
        .ValidationStatus
        .REJECTED
    ):
        new_rejection_reason = (
            assessment.relevance_notes
        )

    if (
        article.processing_status
        == new_processing_status
        and article.rejection_reason
        == new_rejection_reason
    ):
        return

    article.processing_status = new_processing_status
    article.rejection_reason = new_rejection_reason

    article.save(
        update_fields=[
            "processing_status",
            "rejection_reason",
            "updated_at",
        ]
    )


def source_detail(
    request: HttpRequest,
    source_id: int,
) -> HttpResponse:
    source = get_object_or_404(
        _source_readiness_queryset().prefetch_related(
            "seed_urls",
            "url_patterns",
        ),
        id=source_id,
    )

    _attach_source_readiness(source)

    context = {
        "page_title": f"Detail Sumber — {source.name}",
        "active_menu": "sources",
        "source": source,
        "seed_urls": source.seed_urls.all(),
        "url_patterns": source.url_patterns.all(),
    }

    return render(
        request,
        "dashboard/source_detail.html",
        context,
    )


def source_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = SourceForm(request.POST)

        if form.is_valid():
            source = form.save()

            messages.success(
                request,
                f"Sumber {source.name} berhasil ditambahkan.",
            )

            return redirect(
                "dashboard:source-detail",
                source_id=source.id,
            )
    else:
        form = SourceForm()

    return render(
        request,
        "dashboard/source_form.html",
        {
            "page_title": "Tambah Sumber OSINT",
            "active_menu": "sources",
            "form": form,
            "form_title": "Tambah Sumber OSINT",
            "submit_label": "Simpan Sumber",
        },
    )


def source_update(
    request: HttpRequest,
    source_id: int,
) -> HttpResponse:
    source = get_object_or_404(
        Source,
        id=source_id,
    )

    if request.method == "POST":
        form = SourceForm(
            request.POST,
            instance=source,
        )

        if form.is_valid():
            source = form.save()

            messages.success(
                request,
                f"Sumber {source.name} berhasil diperbarui.",
            )

            return redirect(
                "dashboard:source-detail",
                source_id=source.id,
            )
    else:
        form = SourceForm(
            instance=source,
        )

    return render(
        request,
        "dashboard/source_form.html",
        {
            "page_title": "Edit Sumber OSINT",
            "active_menu": "sources",
            "form": form,
            "source": source,
            "form_title": "Edit Sumber OSINT",
            "submit_label": "Simpan Perubahan",
        },
    )


def source_seed_create(
    request: HttpRequest,
    source_id: int,
) -> HttpResponse:
    source = get_object_or_404(
        Source,
        id=source_id,
    )

    if request.method == "POST":
        form = SourceSeedUrlForm(
            request.POST,
            source=source,
        )

        if form.is_valid():
            seed_url = form.save(
                commit=False,
            )

            seed_url.source = source
            seed_url.save()

            messages.success(
                request,
                "URL awal berhasil ditambahkan.",
            )

            return redirect(
                "dashboard:source-detail",
                source_id=source.id,
            )
    else:
        form = SourceSeedUrlForm(
            source=source,
        )

    return render(
        request,
        "dashboard/source_seed_form.html",
        {
            "page_title": "Tambah URL Awal",
            "active_menu": "sources",
            "source": source,
            "form": form,
            "form_title": "Tambah URL Awal",
            "submit_label": "Simpan URL Awal",
        },
    )


def source_seed_update(
    request: HttpRequest,
    source_id: int,
    seed_id,
) -> HttpResponse:
    source = get_object_or_404(
        Source,
        id=source_id,
    )
    seed_url = get_object_or_404(
        SourceSeedUrl,
        id=seed_id,
        source=source,
    )

    if request.method == "POST":
        form = SourceSeedUrlForm(
            request.POST,
            instance=seed_url,
            source=source,
        )

        if form.is_valid():
            form.save()

            messages.success(
                request,
                "URL awal berhasil diperbarui.",
            )

            return redirect(
                "dashboard:source-detail",
                source_id=source.id,
            )
    else:
        form = SourceSeedUrlForm(
            instance=seed_url,
            source=source,
        )

    return render(
        request,
        "dashboard/source_seed_form.html",
        {
            "page_title": "Edit URL Awal",
            "active_menu": "sources",
            "source": source,
            "form": form,
            "form_title": "Edit URL Awal",
            "submit_label": "Simpan Perubahan",
        },
    )


def source_pattern_create(
    request: HttpRequest,
    source_id: int,
) -> HttpResponse:
    source = get_object_or_404(
        Source,
        id=source_id,
    )

    if request.method == "POST":
        form = SourceUrlPatternForm(
            request.POST,
            source=source,
        )

        if form.is_valid():
            pattern = form.save(commit=False)
            pattern.source = source
            pattern.save()

            messages.success(
                request,
                "Pola URL berhasil ditambahkan.",
            )

            return redirect(
                "dashboard:source-detail",
                source_id=source.id,
            )
    else:
        form = SourceUrlPatternForm(
            source=source,
        )

    return render(
        request,
        "dashboard/source_pattern_form.html",
        {
            "page_title": "Tambah Pola URL",
            "active_menu": "sources",
            "source": source,
            "form": form,
            "form_title": "Tambah Pola URL",
            "submit_label": "Simpan Pola URL",
        },
    )


def source_pattern_update(
    request: HttpRequest,
    source_id: int,
    pattern_id,
) -> HttpResponse:
    source = get_object_or_404(
        Source,
        id=source_id,
    )
    pattern = get_object_or_404(
        SourceUrlPattern,
        id=pattern_id,
        source=source,
    )

    if request.method == "POST":
        form = SourceUrlPatternForm(
            request.POST,
            instance=pattern,
            source=source,
        )

        if form.is_valid():
            form.save()

            messages.success(
                request,
                "Pola URL berhasil diperbarui.",
            )

            return redirect(
                "dashboard:source-detail",
                source_id=source.id,
            )
    else:
        form = SourceUrlPatternForm(
            instance=pattern,
            source=source,
        )

    return render(
        request,
        "dashboard/source_pattern_form.html",
        {
            "page_title": "Edit Pola URL",
            "active_menu": "sources",
            "source": source,
            "form": form,
            "form_title": "Edit Pola URL",
            "submit_label": "Simpan Perubahan",
        },
    )
