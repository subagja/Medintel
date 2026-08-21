import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
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
    SourceDiscoveryQuery,
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
    check_source_seed_readiness,
)
from apps.articles.models import Article
from apps.articles.filters import (
    apply_article_filters,
    build_article_filter_options,
)
from apps.accounts.permissions import Roles, has_role, require_role
from apps.sources.origin import (
    ORIGIN_CHOICES,
    apply_origin_filter,
)
from apps.collection.models import (
    CollectionJob,
    CollectionJobItem,
    CollectionSession,
    CollectionWorker,
)
from apps.crawlers.real_crawler import GenericHtmlCrawler
from apps.crawlers.rss_crawler import OfficialRssCrawler
from apps.crawlers.google_news_crawler import (
    GoogleNewsRssCrawler,
    check_google_news_publisher_readiness,
    get_google_news_allowed_sources,
    get_google_news_max_age_days,
)
from apps.crawlers.google_news_queries import (
    build_google_news_disease_scope,
)
from apps.crawlers.services import enqueue_crawler
from apps.crawlers.unified import (
    get_unified_source_readiness,
    start_unified_collection,
)
from apps.assessments.forms import (
    ArticleValidationAssessmentForm,
    ArticleValidationStatusForm,
    NewCountryLocationForm,
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
from apps.locations.models import Location, LocationAlias
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
from apps.dashboard.threat_level import resolve_dashboard_threat_level
from apps.requirements.models import IntelligenceRequirement
from apps.collection.services.queue import (
    request_collection_job_cancel,
    request_collection_job_pause,
    rerun_collection_job,
    resume_collection_job,
)
from apps.collection.services.worker import active_worker_cutoff


logger = logging.getLogger(__name__)


@require_role(*Roles.ALL)
def dashboard_overview(request: HttpRequest) -> HttpResponse:
    from datetime import timedelta
    from django.db.models import Count
    from django.utils import timezone

    from apps.signals.models import Signal
    from apps.assessments.models import EarlyWarning

    total_articles = Article.objects.count()

    # Status pemeriksaan validator adalah sumber utama kartu Dashboard.
    # Article.processing_status menggambarkan tahap pipeline dan dapat tetap
    # PROCESSED meskipun artikel sedang menunggu keputusan validator.
    pending_validation_articles = Article.objects.filter(
        Q(validation_assessment__isnull=True)
        | Q(
            validation_assessment__validation_status=(
                ArticleValidationAssessment
                .ValidationStatus
                .PENDING
            )
        )
    ).count()

    processed_articles = Article.objects.filter(
        processing_status=Article.ProcessingStatus.PROCESSED,
    ).count()

    validated_articles = Article.objects.filter(
        validation_assessment__validation_status=(
            ArticleValidationAssessment.ValidationStatus.VALIDATED
        )
    ).count()

    rejected_articles = Article.objects.filter(
        validation_assessment__validation_status=(
            ArticleValidationAssessment.ValidationStatus.REJECTED
        )
    ).count()

    latest_articles = (
        Article.objects
        .select_related("source")
        .prefetch_related("diseases", "locations")
        .order_by("-crawled_at")[:10]
    )

    ACTIVE_SIGNAL_STATUSES = [
        Signal.Status.VALIDATED,
        Signal.Status.CORRECTED,
        Signal.Status.ESCALATED,
    ]

    active_signals = Signal.objects.filter(
        status__in=ACTIVE_SIGNAL_STATUSES,
    )

    signal_count = Signal.objects.count()

    priority_wilayah_count = (
        active_signals.exclude(primary_location__isnull=True)
        .values("primary_location")
        .distinct()
        .count()
    )

    threat = resolve_dashboard_threat_level()

    # Tren sinyal 7 hari terakhir -- jumlah sinyal baru per hari.
    today = timezone.localdate()
    trend_labels = []
    trend_values = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        trend_labels.append(day.strftime("%d %b"))
        trend_values.append(
            Signal.objects.filter(
                first_detected_at__date=day,
            ).count()
        )

    priority_diseases = (
        active_signals.exclude(primary_disease__isnull=True)
        .values("primary_disease__name")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    priority_locations = (
        active_signals.exclude(primary_location__isnull=True)
        .values("primary_location__name")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    context = {
        "page_title": "Dashboard Ringkasan",
        "active_menu": "dashboard",
        "summary": {
            "total_articles": total_articles,
            "new_articles": pending_validation_articles,
            "processed_articles": processed_articles,
            "validated_articles": validated_articles,
            "rejected_articles": rejected_articles,
        },
        "latest_articles": latest_articles,
        "signal_count": signal_count,
        "priority_wilayah_count": priority_wilayah_count,
        "threat_level": threat.label,
        "threat_level_class": threat.css_class,
        "threat_level_basis": threat.basis,
        "trend_chart_data": {
            "labels": trend_labels,
            "values": trend_values,
        },
        "priority_diseases": priority_diseases,
        "priority_locations": priority_locations,
        "active_warning_count": EarlyWarning.objects.filter(
            is_current=True,
            status=EarlyWarning.Status.ISSUED,
        ).count(),
    }

    return render(
        request,
        "dashboard/index.html",
        context,
    )


def _ready_seed_sources(
    *,
    seed_types: tuple[str, ...],
) -> list[Source]:
    sources = (
        Source.objects.filter(
            seed_urls__is_active=True,
            seed_urls__seed_type__in=seed_types,
        )
        .prefetch_related("seed_urls", "url_patterns")
        .distinct()
        .order_by("name")
    )

    ready_sources = []

    for source in sources:
        source.crawl_readiness = check_source_seed_readiness(
            source,
            seed_types=seed_types,
        )

        if source.crawl_readiness.is_ready:
            ready_sources.append(source)

    return ready_sources


def _ready_html_sources() -> list[Source]:
    return _ready_seed_sources(
        seed_types=(
            SourceSeedUrl.SeedType.LISTING,
            SourceSeedUrl.SeedType.DIRECT,
        ),
    )


def _ready_rss_sources() -> list[Source]:
    return _ready_seed_sources(
        seed_types=(SourceSeedUrl.SeedType.RSS,),
    )


def _ready_google_news_sources() -> list[Source]:
    if not build_google_news_disease_scope().batches:
        return []
    return get_google_news_allowed_sources()


def _crawler_summary():
    """Ringkasan angka Crawler Artikel/Web -- dipakai bersama oleh
    halaman (render awal) dan endpoint polling `crawler_summary_status`
    (supaya kartu ringkasan di atas ikut ter-update otomatis, bukan
    cuma baris tabel di bawahnya).
    """
    all_jobs = CollectionJob.objects.filter(
        job_type__in=(
            CollectionJob.JobType.CRAWLER,
            CollectionJob.JobType.RSS,
            CollectionJob.JobType.GOOGLE_NEWS,
        ),
    )
    totals = all_jobs.aggregate(
        total_created=Sum("total_created"),
        total_rejected=Sum("total_rejected"),
        total_failed=Sum("total_failed"),
    )

    return {
        "total_jobs": all_jobs.count(),
        "running_jobs": all_jobs.filter(
            status=CollectionJob.Status.RUNNING,
        ).count(),
        "queued_jobs": all_jobs.filter(
            status__in=(
                CollectionJob.Status.PENDING,
                CollectionJob.Status.RETRY_WAITING,
            ),
        ).count(),
        "total_created": totals["total_created"] or 0,
        "total_rejected": totals["total_rejected"] or 0,
        "total_failed": totals["total_failed"] or 0,
    }


@require_role(*Roles.ALL)
def crawler_summary_status(request: HttpRequest) -> HttpResponse:
    """Endpoint JSON ringan untuk polling kartu ringkasan Crawler
    Artikel/Web (dipanggil berkala dari JS, terpisah dari
    `crawler_status` yang fokus ke progres per-job)."""
    return JsonResponse(_crawler_summary())


@require_role(*Roles.ALL)
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
            job_type__in=(
                CollectionJob.JobType.CRAWLER,
                CollectionJob.JobType.RSS,
                CollectionJob.JobType.GOOGLE_NEWS,
            ),
        )
        .select_related(
            "source",
            "session",
            "triggered_by",
            "rerun_of",
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
        job_type__in=(
            CollectionJob.JobType.CRAWLER,
            CollectionJob.JobType.RSS,
            CollectionJob.JobType.GOOGLE_NEWS,
        ),
    )
    totals = all_jobs.aggregate(
        total_created=Sum("total_created"),
        total_rejected=Sum("total_rejected"),
        total_failed=Sum("total_failed"),
    )

    # Tren pengambilan artikel 7 hari terakhir -- total artikel baru
    # per hari dari semua job crawler pada hari itu.
    from datetime import timedelta
    from django.utils import timezone

    today = timezone.localdate()
    trend_labels = []
    trend_values = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        trend_labels.append(day.strftime("%d %b"))
        day_total = all_jobs.filter(
            created_at__date=day,
        ).aggregate(total=Sum("total_created"))["total"]
        trend_values.append(day_total or 0)

    trend_chart_data = {
        "labels": trend_labels,
        "values": trend_values,
    }

    paginator = Paginator(jobs, 15)
    page_obj = paginator.get_page(
        request.GET.get("page")
    )

    ready_sources = _ready_html_sources()
    ready_rss_sources = _ready_rss_sources()
    ready_google_news_sources = _ready_google_news_sources()
    unified_source_rows = get_unified_source_readiness()
    google_news_scope = build_google_news_disease_scope()
    ready_indonesia_count = sum(
        1
        for source in ready_sources
        if source.source_type
        != Source.SourceType.INTERNATIONAL_MEDIA
    )

    context = {
        "page_title": "Pengumpulan Artikel",
        "active_menu": "crawler-artikel",
        "ready_sources": ready_sources,
        "ready_rss_sources": ready_rss_sources,
        "ready_google_news_sources": ready_google_news_sources,
        "unified_source_rows": unified_source_rows,
        "unified_indonesia_count": sum(
            1
            for row in unified_source_rows
            if row.source.source_type
            != Source.SourceType.INTERNATIONAL_MEDIA
        ),
        "unified_rss_primary_count": sum(
            1 for row in unified_source_rows if row.rss_ready
        ),
        "unified_html_fallback_count": sum(
            1
            for row in unified_source_rows
            if row.html_ready and not row.rss_ready
        ),
        "unified_html_deep_scan_count": sum(
            1
            for row in unified_source_rows
            if row.html_ready and row.rss_ready
        ),
        "recent_collection_sessions": (
            CollectionSession.objects.select_related(
                "selected_source", "triggered_by"
            )[:5]
        ),
        "active_intelligence_requirements": (
            IntelligenceRequirement.objects.filter(
                status=IntelligenceRequirement.Status.ACTIVE,
                is_active=True,
            ).order_by("-priority", "code")
        ),
        "selected_intelligence_requirement": request.GET.get(
            "requirement", ""
        ),
        "google_news_scope": google_news_scope,
        "google_news_max_age_days": get_google_news_max_age_days(),
        "crawler_candidate_limit_default": max(
            int(getattr(settings, "CRAWLER_CANDIDATE_LIMIT", 30)),
            1,
        ),
        "google_news_article_limit_default": min(
            max(int(getattr(settings, "GOOGLE_NEWS_ARTICLE_LIMIT", 10)), 1),
            1000,
        ),
        "ready_indonesia_count": ready_indonesia_count,
        "ready_rss_indonesia_count": sum(
            1
            for source in ready_rss_sources
            if source.source_type
            != Source.SourceType.INTERNATIONAL_MEDIA
        ),
        "ready_google_news_indonesia_count": sum(
            1
            for source in ready_google_news_sources
            if source.source_type
            != Source.SourceType.INTERNATIONAL_MEDIA
        ),
        "trend_chart_data": trend_chart_data,
        "filter_sources": Source.objects.order_by("name"),
        "job_statuses": CollectionJob.Status.choices,
        "selected_source": source_code,
        "selected_status": status,
        "page_obj": page_obj,
        "summary": _crawler_summary(),
        "active_workers": CollectionWorker.objects.filter(
            status=CollectionWorker.Status.ACTIVE,
            last_heartbeat_at__gte=active_worker_cutoff(),
        ),
    }

    return render(
        request,
        "dashboard/crawler_list.html",
        context,
    )


@require_POST
@require_role(Roles.ADMIN, Roles.ANALYST)
def crawler_unified_run(request: HttpRequest) -> HttpResponse:
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )
    source_code = request.POST.get("source", "all").strip()
    include_google_news = (
        request.POST.get("include_google_news") == "1"
    )
    html_deep_scan = request.POST.get("html_deep_scan") == "1"
    requirement = None
    requirement_id = request.POST.get("requirement_id", "").strip()

    try:
        if requirement_id:
            requirement = IntelligenceRequirement.objects.get(
                pk=requirement_id,
                status=IntelligenceRequirement.Status.ACTIVE,
                is_active=True,
            )
        article_limit = _parse_optional_positive_int(
            request.POST.get("limit", ""),
            label="Maks. artikel diproses per proses kanal",
        )
        candidate_limit = _parse_optional_positive_int(
            request.POST.get("candidate_limit", ""),
            label="Maks. kandidat diperiksa per proses kanal",
        )
        session = start_unified_collection(
            source_code=source_code,
            include_google_news=include_google_news,
            html_deep_scan=html_deep_scan,
            article_limit=article_limit,
            candidate_limit=candidate_limit,
            triggered_by=(
                request.user if request.user.is_authenticated else None
            ),
            trigger_type="user",
            requirement=requirement,
        )
    except IntelligenceRequirement.DoesNotExist:
        error_text = "Kebutuhan intelijen tidak ditemukan atau tidak lagi Aktif."
        if is_ajax:
            return JsonResponse({"error": error_text}, status=400)
        messages.error(request, error_text)
        return redirect("dashboard:crawler-list")
    except ValidationError as exc:
        error_text = exc.messages[0]
        if is_ajax:
            return JsonResponse({"error": error_text}, status=400)
        messages.error(request, error_text)
        return redirect("dashboard:crawler-list")

    detail_url = reverse(
        "dashboard:crawler-session-detail",
        kwargs={"session_id": session.id},
    )
    if is_ajax:
        return JsonResponse(
            {
                "session_id": str(session.id),
                "reference": session.reference,
                "planned_job_count": session.planned_job_count,
                "skipped_job_count": session.skipped_job_count,
                "detail_url": detail_url,
            }
        )

    messages.success(
        request,
        (
            f"{session.reference} masuk antrean dengan "
            f"{session.planned_job_count} proses kanal."
        ),
    )
    return redirect(detail_url)


@require_role(*Roles.ALL)
def crawler_session_detail(
    request: HttpRequest,
    session_id,
) -> HttpResponse:
    session = get_object_or_404(
        CollectionSession.objects.select_related(
            "selected_source", "triggered_by"
        ),
        pk=session_id,
    )
    jobs = list(
        session.jobs.select_related("source").order_by(
            "created_at", "source__name"
        )
    )
    session_status = session.status
    context = {
        "page_title": f"Sesi {session.reference}",
        "active_menu": "crawler-artikel",
        "session": session,
        "session_requirement_links": session.requirement_links.select_related(
            "requirement"
        ),
        "session_status": session_status,
        "session_totals": session.totals,
        "jobs": jobs,
        "crawler_candidate_limit_default": max(
            int(getattr(settings, "CRAWLER_CANDIDATE_LIMIT", 30)),
            1,
        ),
        "google_news_article_limit_default": min(
            max(int(getattr(settings, "GOOGLE_NEWS_ARTICLE_LIMIT", 10)), 1),
            1000,
        ),
        "skipped_jobs": session.metadata.get("skipped", []),
        "is_running": session_status in {
            CollectionJob.Status.PENDING,
            CollectionJob.Status.RUNNING,
            CollectionJob.Status.RETRY_WAITING,
        },
    }
    return render(
        request,
        "dashboard/crawler_session_detail.html",
        context,
    )


@require_role(*Roles.ALL)
def google_news_discovery_settings(
    request: HttpRequest,
) -> HttpResponse:
    """Kebijakan kanal global dan whitelist Source penerbit."""
    disease_scope = build_google_news_disease_scope()
    rows = []
    sources = (
        Source.objects
        .prefetch_related("url_patterns")
        .order_by("name")
    )

    for source in sources:
        readiness = check_google_news_publisher_readiness(source)
        rows.append(
            {
                "source": source,
                "is_ready": readiness.is_ready,
                "errors": readiness.errors,
            }
        )

    context = {
        "page_title": "Pengaturan Google News Discovery",
        "active_menu": "crawler-artikel",
        "google_news_scope": disease_scope,
        "google_news_configuration_rows": rows,
        "allowed_count": sum(1 for row in rows if row["is_ready"]),
        "excluded_count": sum(1 for row in rows if not row["is_ready"]),
        "max_age_days": get_google_news_max_age_days(),
    }
    return render(
        request,
        "dashboard/google_news_discovery_settings.html",
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
@require_role(Roles.ADMIN, Roles.ANALYST)
def crawler_run(request: HttpRequest) -> HttpResponse:
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )
    source_code = request.POST.get(
        "source",
        "",
    ).strip()
    channel = request.POST.get(
        "channel",
        "html",
    ).strip().casefold()

    channel_config = {
        "html": {
            "ready_sources": _ready_html_sources,
            "crawler_class": GenericHtmlCrawler,
            "job_type": CollectionJob.JobType.CRAWLER,
            "label": "HTML",
        },
        "rss": {
            "ready_sources": _ready_rss_sources,
            "crawler_class": OfficialRssCrawler,
            "job_type": CollectionJob.JobType.RSS,
            "label": "RSS resmi",
        },
        "google_news": {
            "ready_sources": _ready_google_news_sources,
            "crawler_class": GoogleNewsRssCrawler,
            "job_type": CollectionJob.JobType.GOOGLE_NEWS,
            "label": "Google News RSS",
        },
    }.get(channel)

    if channel_config is None:
        error_text = "Kanal pengumpulan tidak valid."
        if is_ajax:
            return JsonResponse({"error": error_text}, status=400)
        messages.error(request, error_text)
        return redirect("dashboard:crawler-list")

    try:
        limit = _parse_optional_positive_int(
            request.POST.get("limit", ""),
            label="Maks. artikel diproses",
        )
        candidate_limit = _parse_optional_positive_int(
            request.POST.get("candidate_limit", ""),
            label="Maks. kandidat diperiksa",
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

    ready_sources = channel_config["ready_sources"]()

    if channel == "google_news":
        request_started_at = timezone.now()
        disease_scope = build_google_news_disease_scope()
        if not disease_scope.batches:
            error_text = (
                "Disease Master belum memiliki penyakit surveilans aktif "
                "yang dapat digunakan untuk discovery."
            )
            if is_ajax:
                return JsonResponse({"error": error_text}, status=400)
            messages.error(request, error_text)
            return redirect("dashboard:crawler-list")
        if not ready_sources:
            error_text = (
                "Belum ada Source aktif dan terverifikasi yang memiliki "
                "crawler serta pola URL allow."
            )
            if is_ajax:
                return JsonResponse({"error": error_text}, status=400)
            messages.error(request, error_text)
            return redirect("dashboard:crawler-list")

        already_running = CollectionJob.objects.filter(
            source__isnull=True,
            job_type=CollectionJob.JobType.GOOGLE_NEWS,
            status__in=(
                CollectionJob.Status.PENDING,
                CollectionJob.Status.RUNNING,
                CollectionJob.Status.RETRY_WAITING,
                CollectionJob.Status.PAUSED,
            ),
        ).exists()
        started = []
        skipped = []
        if already_running:
            skipped.append("google-news-global")
        else:
            crawler = GoogleNewsRssCrawler(
                limit=limit,
                candidate_limit=candidate_limit,
            )
            enqueue_crawler(
                crawler,
                triggered_by=(
                    request.user if request.user.is_authenticated else None
                ),
                trigger_type="user",
            )
            started.append("google-news-global")

        if is_ajax:
            return JsonResponse(
                {
                    "started": started,
                    "skipped": skipped,
            "since": request_started_at.isoformat(),
                    "job_type": CollectionJob.JobType.GOOGLE_NEWS,
                }
            )
        if started:
            messages.success(
                request,
                "Google News RSS global masuk antrean.",
            )
        if skipped:
            messages.warning(
                request,
                "Google News RSS global masih aktif dalam antrean dan tidak diduplikasi.",
            )
        return redirect("dashboard:crawler-list")

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
            job_type=channel_config["job_type"],
            status__in=(
                CollectionJob.Status.PENDING,
                CollectionJob.Status.RUNNING,
                CollectionJob.Status.RETRY_WAITING,
                CollectionJob.Status.PAUSED,
            ),
        ).exists()

        if already_running:
            skipped_sources.append(source.code)
            continue

        crawler = channel_config["crawler_class"](
            source_code=source.code,
            limit=limit,
            candidate_limit=candidate_limit,
        )

        # Request hanya menulis job Menunggu. Worker terpisah mengklaim dan
        # mengeksekusinya sehingga restart server web tidak memutus koleksi.
        enqueue_crawler(
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
                "job_type": channel_config["job_type"],
            }
        )

    if started_sources:
        messages.success(
            request,
            (
                f"Kanal {channel_config['label']} masuk antrean untuk "
                f"{len(started_sources)} sumber: "
                + ", ".join(started_sources)
                + ". Status akan diperbarui otomatis pada tabel di bawah."
            ),
        )

    if skipped_sources:
        messages.warning(
            request,
            (
                "Dilewati karena masih aktif dalam antrean: "
                + ", ".join(skipped_sources)
                + "."
            ),
        )

    return redirect("dashboard:crawler-list")


def _serialize_job_status(job: CollectionJob) -> dict:
    status_labels = dict(CollectionJob.Status.choices)
    job_type_labels = dict(CollectionJob.JobType.choices)

    return {
        "id": str(job.id),
        "source_code": (
            job.source.code if job.source_id else "google-news-global"
        ),
        "source_name": (
            job.source.name if job.source_id else "Discovery Lintas Sumber"
        ),
        "job_type": job.job_type,
        "job_type_label": job_type_labels.get(
            job.job_type,
            job.job_type,
        ),
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
            CollectionJob.Status.RETRY_WAITING,
        ),
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "control_requested": job.control_requested,
    }


@require_role(*Roles.ALL)
def crawler_status(request: HttpRequest) -> HttpResponse:
    """Endpoint JSON untuk polling status job crawler dari JavaScript.

    Dua mode pemakaian:
    - `job_ids` (dipisah koma): job yang sudah tampil di tabel, dicek
      progresnya (dipakai polling rutin).
    - `source_codes` (dipisah koma) + `since` (ISO datetime): dipakai
      begitu crawler baru saja dimasukkan lewat AJAX, untuk menemukan
      CollectionJob antrean yang baru terbentuk sebelum
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
    requested_job_type = request.GET.get(
        "job_type",
        CollectionJob.JobType.CRAWLER,
    )
    supported_job_types = {
        CollectionJob.JobType.CRAWLER,
        CollectionJob.JobType.RSS,
        CollectionJob.JobType.GOOGLE_NEWS,
    }

    if requested_job_type not in supported_job_types:
        return JsonResponse({"jobs": []})

    if since and timezone.is_naive(since):
        since = timezone.make_aware(since, timezone.utc)

    if not source_codes or since is None:
        return JsonResponse({"jobs": []})

    found_jobs = []

    for code in source_codes:
        lookup = CollectionJob.objects.filter(
            job_type=requested_job_type,
            created_at__gte=since,
        )
        if (
            code == "google-news-global"
            and requested_job_type == CollectionJob.JobType.GOOGLE_NEWS
        ):
            lookup = lookup.filter(source__isnull=True)
        else:
            lookup = lookup.filter(source__code=code)
        job = (
            lookup
            .select_related("source")
            .order_by("-created_at")
            .first()
        )

        if job:
            found_jobs.append(job)

    return JsonResponse(
        {"jobs": [_serialize_job_status(job) for job in found_jobs]}
    )


@require_role(Roles.ADMIN, Roles.ANALYST)
@require_POST
def crawler_job_cancel(
    request: HttpRequest,
    job_id,
) -> HttpResponse:
    """Minta pembatalan aman pada job antrean atau job berjalan."""
    job = get_object_or_404(CollectionJob, id=job_id)

    try:
        request_collection_job_cancel(job=job, actor=request.user)
    except ValidationError as exc:
        messages.warning(request, exc.messages[0])
        return redirect("dashboard:crawler-job-detail", job_id=job.id)

    messages.success(
        request,
        (
            f"Permintaan pembatalan untuk {job.source.name} dicatat."
            if job.source_id
            else "Permintaan pembatalan Google News RSS global dicatat."
        ),
    )

    return redirect("dashboard:crawler-job-detail", job_id=job.id)


@require_role(Roles.ADMIN, Roles.ANALYST)
@require_POST
def crawler_job_pause(request: HttpRequest, job_id) -> HttpResponse:
    job = get_object_or_404(CollectionJob, id=job_id)
    try:
        request_collection_job_pause(job=job, actor=request.user)
        messages.success(request, "Permintaan jeda berhasil dicatat.")
    except ValidationError as exc:
        messages.warning(request, exc.messages[0])
    return redirect("dashboard:crawler-job-detail", job_id=job.id)


@require_role(Roles.ADMIN, Roles.ANALYST)
@require_POST
def crawler_job_resume(request: HttpRequest, job_id) -> HttpResponse:
    job = get_object_or_404(CollectionJob, id=job_id)
    try:
        resume_collection_job(job=job, actor=request.user)
        messages.success(request, "Proses dikembalikan ke antrean.")
    except ValidationError as exc:
        messages.warning(request, exc.messages[0])
    return redirect("dashboard:crawler-job-detail", job_id=job.id)


@require_role(Roles.ADMIN, Roles.ANALYST)
@require_POST
def crawler_job_rerun(request: HttpRequest, job_id) -> HttpResponse:
    job = get_object_or_404(CollectionJob, id=job_id)
    try:
        rerun = rerun_collection_job(job=job, actor=request.user)
        messages.success(request, "Jalankan ulang berhasil dimasukkan ke antrean.")
    except ValidationError as exc:
        messages.warning(request, exc.messages[0])
        return redirect("dashboard:crawler-job-detail", job_id=job.id)
    return redirect("dashboard:crawler-job-detail", job_id=rerun.id)


@require_role(*Roles.ALL)
def crawler_job_detail(
    request: HttpRequest,
    job_id,
) -> HttpResponse:
    job = get_object_or_404(
        CollectionJob.objects.select_related(
            "source",
            "session",
            "triggered_by",
            "rerun_of",
        ),
        id=job_id,
        job_type__in=(
            CollectionJob.JobType.CRAWLER,
            CollectionJob.JobType.RSS,
            CollectionJob.JobType.GOOGLE_NEWS,
        ),
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

    paginator = Paginator(items, 20)
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
        "job_logs": job.logs.order_by("-created_at")[:100],
        "worker_online": CollectionWorker.objects.filter(
            id=job.worker_id,
            status=CollectionWorker.Status.ACTIVE,
            last_heartbeat_at__gte=active_worker_cutoff(),
        ).exists() if job.worker_id else False,
        "job_reruns": job.reruns.order_by("created_at"),
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


@require_role(*Roles.ALL)
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
        "sources": Paginator(source_rows, 25).get_page(
            request.GET.get("page")
        ),
        "source_types": Source.SourceType.choices,
        "origin_choices": ORIGIN_CHOICES,
        "search_query": search_query,
        "selected_source_type": source_type,
        "selected_origin": origin,
        "selected_status": status,
        "summary": summary,
        "pending_verification_count": Source.objects.filter(
            is_verified=False,
            seed_urls__is_active=True,
            url_patterns__is_active=True,
            url_patterns__pattern_type="allow",
        ).distinct().count(),
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
    article_id=None,
    tab: str,
    eligibility: str,
    filters: dict,
    workspace: str = "queue",
    history_status: str = "all",
    page=None,
) -> str:
    """URL kembali ke workspace Validasi Artikel setelah aksi POST,
    dengan seluruh filter (eligibility + filter artikel baru) tetap
    dipertahankan supaya daftar artikel tidak ter-reset ke tanpa filter.
    """
    params = {
        "workspace": workspace,
        "eligibility": eligibility,
        "history_status": history_status,
        "tab": tab,
    }

    if article_id:
        params["article"] = str(article_id)

    if page:
        params["page"] = page

    for key in (
        "source",
        "disease",
        "location",
        "processing_status",
        "trend",
        "date_field",
        "date_from",
        "date_to",
        "q",
    ):
        value = filters.get(key)
        if value:
            params[key] = value

    query = urlencode(params)

    return f"{reverse('dashboard:article-validation')}?{query}"


@require_role(*Roles.ALL)
def article_validation(request: HttpRequest) -> HttpResponse:
    if request.method == "POST" and not has_role(
        request.user, *Roles.CONTRIBUTORS
    ):
        raise PermissionDenied(
            "Peran Viewer hanya dapat melihat, tidak dapat mengubah "
            "validasi artikel."
        )

    workspace_mode = (
        request.POST.get("workspace")
        or request.GET.get("workspace")
        or "queue"
    )
    if workspace_mode not in {"queue", "history"}:
        workspace_mode = "queue"

    eligibility_filter = (
        request.POST.get("eligibility")
        or request.GET.get("eligibility")
        or "all"
    )
    if eligibility_filter not in {
        "all",
        "eligible",
        "needs_review",
    }:
        eligibility_filter = "all"

    history_status_filter = (
        request.POST.get("history_status")
        or request.GET.get("history_status")
        or "all"
    )
    if history_status_filter not in {
        "all",
        "validated",
        "rejected",
    }:
        history_status_filter = "all"

    articles = _validation_queryset()
    pending_status = (
        ArticleValidationAssessment.ValidationStatus.PENDING
    )
    completed_statuses = {
        ArticleValidationAssessment.ValidationStatus.VALIDATED,
        ArticleValidationAssessment.ValidationStatus.REJECTED,
    }

    if workspace_mode == "history":
        # Eligibility adalah filter antrean. Pada riwayat, pengguna
        # memilih status selesai agar tidak ada filter yang tersembunyi.
        eligibility_filter = "all"
        if history_status_filter == "all":
            articles = articles.filter(
                validation_assessment__validation_status__in=(
                    completed_statuses
                )
            )
        else:
            articles = articles.filter(
                validation_assessment__validation_status=(
                    history_status_filter
                )
            )
        articles = articles.order_by(
            "-validation_assessment__updated_at",
            "-published_at",
            "-crawled_at",
        )
    else:
        history_status_filter = "all"
        articles = articles.filter(
            Q(validation_assessment__isnull=True)
            | Q(
                validation_assessment__validation_status=(
                    pending_status
                )
            )
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

    search_query = request.GET.get("q", "").strip()
    if search_query:
        articles = articles.filter(
            Q(title__icontains=search_query)
            | Q(source__name__icontains=search_query)
        )
    active_filters["q"] = search_query

    # Tetap sediakan banyak artikel per halaman. Panel antrean memiliki scroll
    # internal sehingga hanya beberapa kartu terlihat sekaligus tanpa membuat
    # keseluruhan halaman memanjang.
    paginator = Paginator(articles, 50)
    requested_page = (
        request.POST.get("page")
        or request.GET.get("page")
        or 1
    )
    page_obj = paginator.get_page(requested_page)
    page_articles = list(page_obj.object_list)
    page_obj.object_list = page_articles

    selected_article_id = (
        request.POST.get("article_id")
        or request.GET.get("article")
    )

    if selected_article_id:
        selected_article = articles.filter(
            id=selected_article_id,
        ).first()
        if selected_article is None:
            # URL lama atau artikel yang baru saja berpindah status tidak
            # boleh membuat workspace 404. ID yang benar tetapi sudah di
            # luar tab aktif akan dialihkan ke item pertama pada halaman.
            get_object_or_404(
                _validation_queryset(),
                id=selected_article_id,
            )
            selected_article = (
                page_articles[0]
                if page_articles
                else None
            )
    else:
        selected_article = (
            page_articles[0]
            if page_articles
            else None
        )

    assessment = None
    form = None
    validation_status_form = None
    primary_disease_form = None
    primary_location_form = None
    new_country_form = None
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
            validation_status_form = ArticleValidationStatusForm(
                instance=assessment,
            )
            primary_disease_form = PrimaryArticleDiseaseForm(
                request.POST,
                article=selected_article,
            )
            primary_location_form = PrimaryArticleLocationForm(
                article=selected_article,
            )
            new_country_form = NewCountryLocationForm()

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
                            workspace=workspace_mode,
                            history_status=history_status_filter,
                            page=page_obj.number,
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
            validation_status_form = ArticleValidationStatusForm(
                instance=assessment,
            )
            primary_disease_form = PrimaryArticleDiseaseForm(
                article=selected_article,
            )
            primary_location_form = PrimaryArticleLocationForm(
                request.POST,
                article=selected_article,
            )
            new_country_form = NewCountryLocationForm()

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
                            workspace=workspace_mode,
                            history_status=history_status_filter,
                            page=page_obj.number,
                        )
                    )
        elif (
            request.method == "POST"
            and post_action in {
                "create_primary_country",
                "create_primary_location",
            }
        ):
            active_validation_tab = "location"
            form = ArticleValidationAssessmentForm(
                instance=assessment,
            )
            validation_status_form = ArticleValidationStatusForm(
                instance=assessment,
            )
            primary_disease_form = PrimaryArticleDiseaseForm(
                article=selected_article,
            )
            primary_location_form = PrimaryArticleLocationForm(
                article=selected_article,
            )
            new_country_form = NewCountryLocationForm(request.POST)

            if not request.user.is_authenticated:
                new_country_form.add_error(
                    None,
                    "Pengguna harus login untuk menambahkan negara.",
                )
            elif new_country_form.is_valid():
                location_level = new_country_form.cleaned_data[
                    "location_level"
                ]
                location_name = new_country_form.cleaned_data[
                    "location_name"
                ]
                country_name = new_country_form.cleaned_data["country_name"]
                country_code = new_country_form.cleaned_data[
                    "country_code"
                ]
                existing_country = (
                    Location.objects.filter(
                        administrative_level=(
                            Location.AdministrativeLevel.COUNTRY
                        ),
                        country_code=country_code,
                        parent__isnull=True,
                    )
                    .order_by("-is_active", "name")
                    .first()
                )

                with transaction.atomic():
                    if existing_country is None:
                        country = Location.objects.create(
                            name=country_name,
                            code=f"ISO-{country_code}",
                            administrative_level=(
                                Location.AdministrativeLevel.COUNTRY
                            ),
                            country_code=country_code,
                            is_active=True,
                        )
                        country_created = True
                    else:
                        country = existing_country
                        country_created = False
                        if not country.is_active:
                            country.is_active = True
                            country.save(update_fields=["is_active", "updated_at"])

                    if location_level == Location.AdministrativeLevel.COUNTRY:
                        location = country
                        location_created = country_created
                    else:
                        location = (
                            Location.objects.filter(
                                name__iexact=location_name,
                                administrative_level=location_level,
                                parent=country,
                                country_code=country_code,
                            )
                            .order_by("-is_active", "name")
                            .first()
                        )
                        if location is None:
                            location = Location.objects.create(
                                name=location_name,
                                administrative_level=location_level,
                                parent=country,
                                country_code=country_code,
                                is_active=True,
                            )
                            location_created = True
                        else:
                            location_created = False
                            if not location.is_active:
                                location.is_active = True
                                location.save(
                                    update_fields=["is_active", "updated_at"]
                                )

                    correction_result = set_primary_article_location(
                        article=selected_article,
                        location=location,
                        reviewer=request.user,
                        notes=new_country_form.cleaned_data[
                            "country_notes"
                        ],
                    )

                if location_created:
                    messages.success(
                        request,
                        (
                            f"Lokasi {location} ({country_code}) "
                            "ditambahkan dan ditetapkan sebagai lokasi utama."
                        ),
                    )
                else:
                    messages.info(
                        request,
                        (
                            f"Lokasi {location} sudah terdaftar; "
                            "lokasi tersebut ditetapkan "
                            "sebagai lokasi utama."
                        ),
                    )

                if not correction_result.changed:
                    messages.info(
                        request,
                        "Lokasi utama artikel tidak berubah.",
                    )

                return redirect(
                    _build_validation_redirect_url(
                        article_id=selected_article.id,
                        tab="location",
                        eligibility=eligibility_filter,
                        filters=active_filters,
                        workspace=workspace_mode,
                        history_status=history_status_filter,
                        page=page_obj.number,
                    )
                )
        else:
            primary_disease_form = PrimaryArticleDiseaseForm(
                article=selected_article,
            )
            primary_location_form = PrimaryArticleLocationForm(
                article=selected_article,
            )
            new_country_form = NewCountryLocationForm()

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

                if post_action == "save_validation_status":
                    active_validation_tab = "validation"
                    validation_status_form = (
                        ArticleValidationStatusForm(
                            request.POST,
                            instance=assessment,
                        )
                    )
                    processing_form = validation_status_form
                    # Field Neraca Informasi tetap memakai nilai database
                    # dan tidak ikut divalidasi saat tombol tab Validasi
                    # ditekan.
                    form = ArticleValidationAssessmentForm(
                        instance=assessment,
                    )
                else:
                    form = ArticleValidationAssessmentForm(
                        request.POST,
                        instance=assessment,
                    )
                    validation_status_form = (
                        ArticleValidationStatusForm(
                            instance=assessment,
                        )
                    )
                    processing_form = form

                if (
                    processing_form.is_valid()
                    and processing_form.cleaned_data["validation_status"]
                    == ArticleValidationAssessment
                    .ValidationStatus
                    .VALIDATED
                ):
                    readiness_errors = []
                    if not selected_diseases:
                        readiness_errors.append(
                            "Artikel belum memiliki hasil ekstraksi penyakit."
                        )
                    if not selected_locations:
                        readiness_errors.append(
                            "Artikel belum memiliki hasil ekstraksi lokasi."
                        )

                    has_numeric_fact = any(
                        fact.case_count is not None
                        or fact.death_count is not None
                        for fact in selected_facts
                    )
                    has_linked_numeric_fact = any(
                        (
                            fact.case_count is not None
                            or fact.death_count is not None
                        )
                        and fact.disease_id
                        and fact.location_id
                        and fact.validation_status
                        != ValidationStatus.REJECTED
                        for fact in selected_facts
                    )
                    if has_numeric_fact and not has_linked_numeric_fact:
                        readiness_errors.append(
                            "Fakta numerik belum dapat digunakan. Pastikan "
                            "fakta tidak ditolak serta sudah ditautkan ke "
                            "penyakit dan lokasi."
                        )

                    if readiness_errors:
                        processing_form.add_error(
                            None,
                            ValidationError(readiness_errors),
                        )

                if processing_form.is_valid():
                    with transaction.atomic():
                        saved_assessment = processing_form.save(
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
                                    saved_assessment.relevance_notes
                                    if post_action
                                    == "save_validation_status"
                                    else saved_assessment.assessment_notes
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

                    if post_action == "save_validation_status":
                        messages.success(
                            request,
                            "Status validasi artikel berhasil disimpan.",
                        )
                    else:
                        messages.success(
                            request,
                            (
                                "Penilaian artikel berhasil disimpan "
                                f"dengan nilai "
                                f"{saved_assessment.admiralty_code}."
                            ),
                        )

                    if (
                        generation_summary is not None
                        and generation_summary["analysis_mode"]
                        == "qualitative"
                    ):
                        messages.info(
                            request,
                            (
                                "Artikel tervalidasi sebagai informasi "
                                "kualitatif. Artikel tetap disimpan sebagai "
                                "sumber pendukung, tetapi tidak membentuk "
                                "indikator kasus, sinyal kuantitatif, atau "
                                "nilai peta karena tidak memiliki jumlah "
                                "kasus/kematian."
                            ),
                        )
                    elif generation_summary is not None:
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

                    saved_is_completed = (
                        saved_assessment.validation_status
                        in completed_statuses
                    )
                    target_workspace = workspace_mode
                    target_history_status = history_status_filter
                    target_article_id = selected_article.id

                    if saved_is_completed and workspace_mode == "queue":
                        # Artikel selesai langsung keluar dari antrean.
                        # Tanpa article ID, halaman otomatis memilih item
                        # antrean berikutnya untuk melanjutkan pekerjaan.
                        target_article_id = None
                        target_workspace = "queue"
                        target_history_status = "all"
                        messages.info(
                            request,
                            (
                                "Artikel telah dipindahkan ke Riwayat "
                                "Validasi dan tidak lagi tampil di antrean."
                            ),
                        )
                    elif saved_is_completed:
                        # Jika status selesai diubah dari halaman riwayat,
                        # tampilkan lagi itemnya tanpa terjebak subfilter lama.
                        target_workspace = "history"
                        target_history_status = "all"
                    else:
                        # Mengembalikan artikel selesai ke Perlu Tinjau akan
                        # memindahkannya kembali ke antrean aktif.
                        target_workspace = "queue"
                        target_history_status = "all"

                    return redirect(
                        _build_validation_redirect_url(
                            article_id=target_article_id,
                            tab=active_validation_tab,
                            eligibility=eligibility_filter,
                            filters=active_filters,
                            workspace=target_workspace,
                            history_status=target_history_status,
                            page=page_obj.number,
                        )
                    )
            else:
                form = ArticleValidationAssessmentForm(
                    instance=assessment,
                )
                validation_status_form = ArticleValidationStatusForm(
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

    queue_articles = base_articles.filter(
        Q(validation_assessment__isnull=True)
        | Q(
            validation_assessment__validation_status=(
                ArticleValidationAssessment
                .ValidationStatus
                .PENDING
            )
        )
    )
    pending_count = queue_articles.count()

    eligible_count = queue_articles.filter(
        has_extracted_disease=True,
        has_extracted_location=True,
        has_numeric_fact=True,
    ).count()

    needs_review_count = (
        queue_articles
        .filter(
            Q(has_extracted_disease=False)
            | Q(has_extracted_location=False)
            | Q(has_numeric_fact=False)
        )
        .count()
    )

    # Badge pada tab workspace harus mengikuti filter lanjutan dan pencarian
    # yang dipakai daftar artikel. Ringkasan kartu di bagian atas tetap
    # bersifat global, sedangkan workspace_summary menjelaskan jumlah hasil
    # dalam konteks filter saat ini. Tanpa pemisahan ini badge dapat tetap
    # menampilkan ratusan artikel ketika daftar sebenarnya kosong karena
    # filter aktif.
    filtered_summary_articles, _unused_filters = apply_article_filters(
        base_articles,
        request.GET,
    )
    if search_query:
        filtered_summary_articles = filtered_summary_articles.filter(
            Q(title__icontains=search_query)
            | Q(source__name__icontains=search_query)
        )

    filtered_queue_articles = filtered_summary_articles.filter(
        Q(validation_assessment__isnull=True)
        | Q(
            validation_assessment__validation_status=(
                ArticleValidationAssessment
                .ValidationStatus
                .PENDING
            )
        )
    )
    filtered_history_articles = filtered_summary_articles.filter(
        validation_assessment__validation_status__in=(
            completed_statuses
        )
    )

    workspace_summary = {
        "pending": filtered_queue_articles.count(),
        "completed": filtered_history_articles.count(),
        "validated": filtered_history_articles.filter(
            validation_assessment__validation_status=(
                ArticleValidationAssessment
                .ValidationStatus
                .VALIDATED
            )
        ).count(),
        "rejected": filtered_history_articles.filter(
            validation_assessment__validation_status=(
                ArticleValidationAssessment
                .ValidationStatus
                .REJECTED
            )
        ).count(),
        "eligible": filtered_queue_articles.filter(
            has_extracted_disease=True,
            has_extracted_location=True,
            has_numeric_fact=True,
        ).count(),
        "needs_review": filtered_queue_articles.filter(
            Q(has_extracted_disease=False)
            | Q(has_extracted_location=False)
            | Q(has_numeric_fact=False)
        ).count(),
        # Hasil daftar setelah workspace/status/eligibility ikut diterapkan.
        "visible": paginator.count,
    }

    meaningful_filter_keys = {
        "source",
        "disease",
        "location",
        "processing_status",
        "trend",
        "date_from",
        "date_to",
        "q",
    }
    has_active_article_filters = any(
        active_filters.get(key)
        for key in meaningful_filter_keys
    ) or active_filters.get("date_field") not in {
        "",
        "published_at",
        None,
    }
    has_active_list_filters = (
        has_active_article_filters
        or (
            workspace_mode == "queue"
            and eligibility_filter != "all"
        )
        or (
            workspace_mode == "history"
            and history_status_filter != "all"
        )
    )

    context = {
        "page_title": "Validasi Artikel",
        "active_menu": "article_validation",
        "articles": page_articles,
        "page_obj": page_obj,
        "selected_article": selected_article,
        "assessment": assessment,
        "assessment_form": form,
        "validation_status_form": validation_status_form,
        "primary_disease_form": primary_disease_form,
        "primary_location_form": primary_location_form,
        "new_country_form": new_country_form,
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
        "workspace_mode": workspace_mode,
        "eligibility_filter": eligibility_filter,
        "history_status_filter": history_status_filter,
        "search_query": search_query,
        "active_filters": active_filters,
        "extra_filter_qs": _extra_filter_querystring(active_filters),
        "workspace_summary": workspace_summary,
        "has_active_list_filters": has_active_list_filters,
        **build_article_filter_options(),
        "summary": {
            "total": pending_count,
            "all": total_articles,
            "pending": pending_count,
            "completed": validated_count + rejected_count,
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


@require_role(Roles.ADMIN, Roles.ANALYST)
@require_POST
def article_delete(
    request: HttpRequest,
    article_id,
) -> HttpResponse:
    """Hapus artikel yang sudah ditandai "Tidak Relevan" oleh analis.

    Dibatasi hanya untuk artikel REJECTED (bukan sembarang artikel)
    supaya tidak sengaja menghapus data yang masih relevan untuk
    surveilans. Kalau artikel ternyata sudah dipakai sebagai bukti
    sinyal/assessment (FK on_delete=PROTECT), penghapusan ditolak
    dengan pesan yang jelas -- bukan error mentah.
    """
    from django.db.models.deletion import ProtectedError

    article = get_object_or_404(Article, id=article_id)

    if article.processing_status != Article.ProcessingStatus.REJECTED:
        messages.error(
            request,
            (
                "Hanya artikel berstatus \"Tidak Relevan\" yang bisa "
                "dihapus lewat halaman ini."
            ),
        )
        return redirect(request.POST.get("next") or "dashboard:article-list")

    article_title = article.title

    try:
        article.delete()
    except ProtectedError:
        messages.error(
            request,
            (
                f'"{article_title}" tidak bisa dihapus karena sudah '
                "dipakai sebagai bukti pada sinyal intelijen atau "
                "evaluasi informasi. Hapus/lepaskan keterkaitan itu "
                "dulu kalau memang perlu dihapus."
            ),
        )
        return redirect(request.POST.get("next") or "dashboard:article-list")

    messages.success(
        request,
        f'Artikel "{article_title}" berhasil dihapus.',
    )

    return redirect(request.POST.get("next") or "dashboard:article-list")


@require_role(*Roles.ALL)
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

    has_numeric_fact = any(
        fact.case_count is not None
        or fact.death_count is not None
        for fact in facts
    )

    summary = {
        "analysis_mode": (
            "quantitative" if has_numeric_fact else "qualitative"
        ),
        "quantitative_eligible": has_numeric_fact,
        "qualitative_reason": (
            "Artikel relevan, tetapi tidak mencantumkan jumlah kasus "
            "atau kematian."
            if not has_numeric_fact
            else ""
        ),
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

    # Artikel relevan tanpa angka tetap sah sebagai informasi kualitatif.
    # Berhenti sebelum validasi fakta/generasi indikator agar artikel ini
    # tidak masuk hitungan kasus, rasio penduduk, peta, atau sinyal berbasis
    # indikator kuantitatif.
    if not has_numeric_fact:
        summary["facts_skipped"] = len(facts)
        return summary

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


@require_role(*Roles.ALL)
def source_detail(
    request: HttpRequest,
    source_id: int,
) -> HttpResponse:
    source = get_object_or_404(
        _source_readiness_queryset().prefetch_related(
            "seed_urls",
            "url_patterns",
            "discovery_queries",
        ),
        id=source_id,
    )

    _attach_source_readiness(source)
    google_news_readiness = check_google_news_publisher_readiness(source)

    context = {
        "page_title": f"Detail Sumber — {source.name}",
        "active_menu": "sources",
        "source": source,
        "seed_urls": source.seed_urls.all(),
        "url_patterns": source.url_patterns.all(),
        "google_news_readiness": google_news_readiness,
    }

    return render(
        request,
        "dashboard/source_detail.html",
        context,
    )


@require_role(Roles.ADMIN)
def source_verification_queue(request: HttpRequest) -> HttpResponse:
    """Daftar sumber yang sudah punya seed URL + pola allow aktif
    (secara teknis siap dites) tapi belum diverifikasi manusia
    (`is_verified=False`). Dipakai untuk menindaklanjuti sumber draft
    (mis. hasil kurasi media nasional besar) satu per satu.
    """
    sources = (
        _source_readiness_queryset()
        .filter(
            is_verified=False,
            active_seed_count__gt=0,
            active_pattern_count__gt=0,
        )
        .prefetch_related(
            "seed_urls",
            "url_patterns",
        )
        .order_by("name")
    )

    rows = []

    for source in sources:
        _attach_source_readiness(source)
        rows.append(source)

    context = {
        "page_title": "Verifikasi Sumber",
        "active_menu": "source_verification",
        "sources": rows,
    }

    return render(
        request,
        "dashboard/source_verification_queue.html",
        context,
    )


@require_POST
@require_role(Roles.ADMIN)
def source_verification_mark_reviewed(
    request: HttpRequest,
    source_id: int,
) -> HttpResponse:
    """Aksi cepat dari antrean verifikasi: tandai sumber terverifikasi,
    opsional sekaligus aktifkan crawling kalau syarat lain sudah
    terpenuhi (is_active + seed aktif + pola allow aktif).
    """
    source = get_object_or_404(
        Source,
        id=source_id,
    )

    also_enable_crawl = (
        request.POST.get("enable_crawl") == "1"
    )

    source.is_verified = True

    if not source.verified_at:
        source.verified_at = timezone.now()

    update_fields = ["is_verified", "verified_at", "updated_at"]

    if also_enable_crawl:
        readiness = check_source_crawl_readiness(source)
        non_crawl_errors = [
            error
            for error in readiness.errors
            if "Crawling belum diaktifkan" not in error
        ]

        if not non_crawl_errors:
            source.crawl_enabled = True
            update_fields.append("crawl_enabled")
        else:
            messages.warning(
                request,
                (
                    f"{source.name} ditandai terverifikasi, tapi "
                    "crawling belum diaktifkan otomatis karena: "
                    + " ".join(non_crawl_errors)
                ),
            )

    source.save(update_fields=update_fields)

    messages.success(
        request,
        f"{source.name} ditandai terverifikasi.",
    )

    return redirect("dashboard:source-verification-queue")


@require_role(Roles.ADMIN)
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


@require_role(Roles.ADMIN)
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


@require_role(Roles.ADMIN)
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


@require_role(Roles.ADMIN)
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


@require_role(Roles.ADMIN)
def source_discovery_query_create(
    request: HttpRequest,
    source_id: int,
) -> HttpResponse:
    get_object_or_404(Source, id=source_id)
    messages.info(
        request,
        (
            "Pengaturan Google News telah dipindahkan ke menu Pengumpulan "
            "Artikel dan istilah penyakit dibentuk otomatis dari Disease "
            "Master."
        ),
    )
    return redirect("dashboard:crawler-list")


@require_role(Roles.ADMIN)
def source_discovery_query_update(
    request: HttpRequest,
    source_id: int,
    query_id,
) -> HttpResponse:
    get_object_or_404(
        SourceDiscoveryQuery,
        id=query_id,
        source_id=source_id,
    )
    messages.info(
        request,
        (
            "Kueri manual tidak lagi digunakan. Atur kanal Google News pada "
            "menu Pengumpulan Artikel."
        ),
    )
    return redirect("dashboard:crawler-list")


@require_POST
@require_role(Roles.ADMIN)
def google_news_discovery_configure(
    request: HttpRequest,
) -> HttpResponse:
    messages.info(
        request,
        (
            "Google News sekarang berjalan sebagai discovery global. Source "
            "otomatis masuk whitelist bila aktif, terverifikasi, crawling "
            "aktif, dan memiliki pola URL allow."
        ),
    )
    return redirect("dashboard:google-news-discovery-settings")


@require_role(Roles.ADMIN)
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


@require_role(Roles.ADMIN)
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


@require_role(*Roles.ALL)
def location_list(request: HttpRequest) -> HttpResponse:
    """Halaman "Geocoding & Lokasi": ringkasan kesehatan data master
    wilayah (provinsi/kabupaten/kota/dst) dan daftar lokasi yang bisa
    dicari/difilter, dengan jumlah alias dan penyebutan artikel.
    """
    from django.db.models import Count, Q as DjangoQ

    locations = (
        Location.objects.annotate(
            alias_count=Count("aliases", distinct=True),
            mention_count=Count("article_mentions", distinct=True),
        )
        .select_related("parent")
    )

    search_query = request.GET.get("q", "").strip()
    level_filter = request.GET.get("level", "").strip()
    coordinate_filter = request.GET.get("coordinates", "").strip()

    if search_query:
        locations = locations.filter(
            DjangoQ(name__icontains=search_query)
            | DjangoQ(code__icontains=search_query)
            | DjangoQ(aliases__alias__icontains=search_query)
        ).distinct()

    valid_levels = {
        value for value, _label in Location.AdministrativeLevel.choices
    }
    if level_filter in valid_levels:
        locations = locations.filter(administrative_level=level_filter)

    if coordinate_filter == "missing":
        locations = locations.filter(
            DjangoQ(latitude__isnull=True) | DjangoQ(longitude__isnull=True)
        )
    elif coordinate_filter == "present":
        locations = locations.filter(
            latitude__isnull=False,
            longitude__isnull=False,
        )

    locations = locations.order_by("administrative_level", "name")

    paginator = Paginator(locations, 30)
    page_obj = paginator.get_page(request.GET.get("page"))

    all_locations = Location.objects.all()
    summary = {
        "total": all_locations.count(),
        "by_level": list(
            all_locations.values("administrative_level")
            .annotate(count=Count("id"))
            .order_by("administrative_level")
        ),
        "missing_coordinates": all_locations.filter(
            DjangoQ(latitude__isnull=True) | DjangoQ(longitude__isnull=True)
        ).count(),
        "inactive": all_locations.filter(is_active=False).count(),
        "total_aliases": LocationAlias.objects.filter(
            is_active=True
        ).count(),
    }

    level_labels = dict(Location.AdministrativeLevel.choices)
    for row in summary["by_level"]:
        row["label"] = level_labels.get(
            row["administrative_level"],
            row["administrative_level"],
        )

    context = {
        "page_title": "Geocoding & Lokasi",
        "active_menu": "geocoding",
        "page_obj": page_obj,
        "summary": summary,
        "level_choices": Location.AdministrativeLevel.choices,
        "search_query": search_query,
        "selected_level": level_filter,
        "selected_coordinates": coordinate_filter,
    }

    return render(
        request,
        "dashboard/location_list.html",
        context,
    )


@require_role(*Roles.ALL)
def spread_map(request: HttpRequest) -> HttpResponse:
    """Halaman peta persebaran penyakit berbasis timeline (provinsi
    atau kabupaten/kota, bisa di-toggle), dibangun dari ArticleFact
    yang punya event_date + lokasi + penyakit lengkap.
    """
    accepted_extraction_statuses = (
        ValidationStatus.VALIDATED,
        ValidationStatus.CORRECTED,
    )
    domestic_disease_ids = Disease.objects.filter(
        article_facts__event_date__isnull=False,
        article_facts__location__isnull=False,
        article_facts__location__country_code="ID",
        article_facts__case_count__gt=0,
        article_facts__validation_status__in=(
            accepted_extraction_statuses
        ),
    ).values_list("id", flat=True)
    foreign_article_ids = ArticleLocation.objects.filter(
        is_primary=True,
        validation_status__in=accepted_extraction_statuses,
    ).exclude(
        location__country_code="ID",
    ).values_list("article_id", flat=True)
    foreign_disease_ids = Disease.objects.filter(
        article_mentions__is_primary=True,
        article_mentions__validation_status__in=(
            accepted_extraction_statuses
        ),
        article_mentions__article__validation_assessment__validation_status=(
            ArticleValidationAssessment.ValidationStatus.VALIDATED
        ),
        article_mentions__article_id__in=foreign_article_ids,
    ).values_list("id", flat=True)

    disease_ids = set(domestic_disease_ids) | set(foreign_disease_ids)
    diseases = Disease.objects.filter(
        id__in=disease_ids,
    ).order_by("name")

    context = {
        "page_title": "Peta Sebaran Penyakit",
        "active_menu": "spread_map",
        "diseases": diseases,
    }

    return render(
        request,
        "dashboard/spread_map.html",
        context,
    )


@require_role(*Roles.ALL)
def spread_map_data(request: HttpRequest) -> HttpResponse:
    """API timeline metrik sebaran artikel untuk satu penyakit.

    Provinsi dinormalisasi memakai penduduk BPS 2025. Kabupaten/kota
    tetap memakai jumlah kasus terlapor karena denominator resmi yang
    lengkap belum dipasang. Setiap frame memakai jendela berjalan 14
    hari supaya angka lama tidak terus menumpuk tanpa batas.
    """
    from datetime import timedelta

    from .spread_map_metrics import (
        MAP_MODE_CUMULATIVE,
        MAP_MODE_ROLLING,
        ROLLING_WINDOW_DAYS,
        metric_metadata,
        normalize_bps_code,
        province_population,
        rate_per_100k,
    )

    disease_code = request.GET.get("disease", "").strip()
    scope = request.GET.get("scope", "domestic").strip()
    level = request.GET.get("level", "province").strip()
    mode = request.GET.get("mode", MAP_MODE_CUMULATIVE).strip()

    if scope == "global":
        return JsonResponse(
            _spread_map_global_payload(
                disease_code=disease_code,
                mode=mode,
            )
        )

    if level not in ("province", "regency_city"):
        level = "province"
    if mode not in (MAP_MODE_CUMULATIVE, MAP_MODE_ROLLING):
        mode = MAP_MODE_CUMULATIVE

    target_levels = (
        {Location.AdministrativeLevel.PROVINCE}
        if level == "province"
        else {
            Location.AdministrativeLevel.REGENCY,
            Location.AdministrativeLevel.CITY,
        }
    )

    facts = (
        ArticleFact.objects.filter(
            event_date__isnull=False,
            location__isnull=False,
            location__country_code="ID",
            case_count__gt=0,
            validation_status__in=(
                ValidationStatus.VALIDATED,
                ValidationStatus.CORRECTED,
            ),
        )
        .select_related(
            "article",
            "article__source",
            "disease",
            "location",
            "location__parent",
            "location__parent__parent",
            "location__parent__parent__parent",
        )
    )

    if disease_code:
        facts = facts.filter(disease__code=disease_code)

    facts = list(facts.order_by("event_date"))
    metric = metric_metadata(level, mode)

    if not facts:
        return JsonResponse(
            {
                "timeline": [],
                "locations": {},
                "level": level,
                "mode": mode,
                "metric": metric,
            }
        )

    def resolve_ancestor(location):
        """Naik rantai parent sampai ketemu level target, None kalau
        tidak ketemu (mis. lokasi luar negeri atau data tidak lengkap).
        """
        current = location
        hops = 0
        while current is not None and hops < 6:
            if current.administrative_level in target_levels:
                return current
            current = current.parent
            hops += 1
        return None

    # Cache resolusi supaya tidak mengulang jalan yang sama untuk
    # lokasi yang muncul di banyak ArticleFact.
    ancestor_cache = {}
    locations_meta = {}

    def get_ancestor(location):
        if location.id not in ancestor_cache:
            ancestor_cache[location.id] = resolve_ancestor(location)
        return ancestor_cache[location.id]

    def location_code(location):
        normalized_code = normalize_bps_code(location.code)
        return normalized_code or str(location.id)

    earliest = facts[0].event_date
    latest = max(f.event_date for f in facts)

    # Bucket mingguan dari tanggal paling awal ke paling akhir.
    buckets = []
    cursor = earliest
    while cursor <= latest:
        buckets.append(cursor)
        cursor = cursor + timedelta(days=7)
    if not buckets or buckets[-1] < latest:
        buckets.append(latest)

    timeline = []

    for bucket_end in buckets:
        window_start = (
            earliest
            if mode == MAP_MODE_CUMULATIVE
            else bucket_end - timedelta(days=ROLLING_WINDOW_DAYS - 1)
        )
        new_since = bucket_end - timedelta(days=6)
        frame_groups = {}
        seen_reported_values = set()
        seen_death_values = set()

        for fact in facts:
            if fact.event_date < window_start:
                continue
            if fact.event_date > bucket_end:
                break

            ancestor = get_ancestor(fact.location)
            if ancestor is None:
                continue

            code = location_code(ancestor)
            population_reference = (
                province_population(ancestor.code)
                if level == "province"
                else None
            )
            locations_meta.setdefault(
                code,
                {
                    "name": ancestor.name,
                    "id": str(ancestor.id),
                    "code": code,
                    "administrative_level": (
                        ancestor.administrative_level
                    ),
                    "population": (
                        population_reference["population"]
                        if population_reference
                        else None
                    ),
                    "population_reference_year": (
                        population_reference["reference_year"]
                        if population_reference
                        else None
                    ),
                },
            )

            entry = frame_groups.setdefault(
                code,
                {
                    "reported_case_count": 0,
                    "reported_death_count": 0,
                    "article_ids": set(),
                    "sources": set(),
                    "is_new": False,
                    "population_reference": population_reference,
                },
            )
            entry["article_ids"].add(str(fact.article_id))
            if fact.article.source_id:
                entry["sources"].add(fact.article.source.name)

            # Heuristik minimum: angka yang sama untuk penyakit, lokasi
            # asli, dan tanggal kejadian yang sama dihitung satu kali,
            # walaupun diberitakan ulang oleh beberapa artikel.
            reported_value_key = (
                str(fact.disease_id),
                str(fact.location_id),
                fact.event_date,
                fact.case_count,
            )
            if reported_value_key not in seen_reported_values:
                seen_reported_values.add(reported_value_key)
                entry["reported_case_count"] += fact.case_count

            if fact.death_count:
                death_value_key = (
                    str(fact.disease_id),
                    str(fact.location_id),
                    fact.event_date,
                    fact.death_count,
                )
                if death_value_key not in seen_death_values:
                    seen_death_values.add(death_value_key)
                    entry["reported_death_count"] += fact.death_count

            if (
                fact.trend == ArticleFact.Trend.NEW_OCCURRENCE
                and fact.event_date >= new_since
            ):
                entry["is_new"] = True

        frame_locations = {}
        for code, data in frame_groups.items():
            case_count = data["reported_case_count"]
            if case_count <= 0:
                continue

            population = None
            metric_value = float(case_count)
            if level == "province" and mode == MAP_MODE_ROLLING:
                population_reference = data["population_reference"]
                population = (
                    population_reference["population"]
                    if population_reference
                    else None
                )
                metric_value = rate_per_100k(case_count, population)

            frame_locations[code] = {
                # case_count dipertahankan untuk kompatibilitas klien lama.
                "case_count": case_count,
                "reported_case_count": case_count,
                "reported_death_count": data["reported_death_count"],
                "metric_value": metric_value,
                "population": population,
                "article_count": len(data["article_ids"]),
                "source_count": len(data["sources"]),
                "sources": sorted(data["sources"]),
                "is_new": data["is_new"],
            }

        timeline.append(
            {
                "date": bucket_end.isoformat(),
                "period_start": window_start.isoformat(),
                "period_end": bucket_end.isoformat(),
                "locations": frame_locations,
            }
        )

    return JsonResponse(
        {
            "timeline": timeline,
            "locations": locations_meta,
            "level": level,
            "mode": mode,
            "metric": metric,
        }
    )


def _spread_map_global_payload(
    *,
    disease_code: str,
    mode: str,
) -> dict:
    """Bangun timeline marker luar negeri tanpa denominator BPS.

    Ukuran marker menunjukkan banyaknya artikel tervalidasi. Angka kasus
    tidak dijumlahkan antarartikel karena artikel berikutnya bisa merupakan
    pembaruan angka kumulatif yang sama; API hanya mengirim angka tervalidasi
    terbaru pada setiap lokasi.
    """
    from datetime import timedelta

    from .spread_map_metrics import (
        MAP_MODE_CUMULATIVE,
        MAP_MODE_ROLLING,
        ROLLING_WINDOW_DAYS,
    )

    if mode not in (MAP_MODE_CUMULATIVE, MAP_MODE_ROLLING):
        mode = MAP_MODE_CUMULATIVE

    accepted_statuses = (
        ValidationStatus.VALIDATED,
        ValidationStatus.CORRECTED,
    )
    relations = (
        ArticleLocation.objects.filter(
            is_primary=True,
            validation_status__in=accepted_statuses,
            article__validation_assessment__validation_status=(
                ArticleValidationAssessment.ValidationStatus.VALIDATED
            ),
            location__latitude__isnull=False,
            location__longitude__isnull=False,
        )
        .exclude(location__country_code="ID")
        .select_related(
            "article",
            "article__source",
            "location",
            "location__parent",
            "location__parent__parent",
        )
    )
    if disease_code:
        relations = relations.filter(
            article__article_diseases__disease__code=disease_code,
            article__article_diseases__is_primary=True,
            article__article_diseases__validation_status__in=(
                accepted_statuses
            ),
        )

    relations = list(relations.distinct())
    article_ids = [relation.article_id for relation in relations]
    primary_diseases = {
        relation.article_id: relation.disease
        for relation in ArticleDisease.objects.filter(
            article_id__in=article_ids,
            is_primary=True,
            validation_status__in=accepted_statuses,
        ).select_related("disease")
    }
    if disease_code:
        primary_diseases = {
            article_id: disease
            for article_id, disease in primary_diseases.items()
            if disease.code == disease_code
        }

    validated_facts = list(
        ArticleFact.objects.filter(
            article_id__in=article_ids,
            validation_status__in=accepted_statuses,
        )
        .filter(Q(case_count__isnull=False) | Q(death_count__isnull=False))
        .select_related("disease", "location")
        .order_by("event_date", "created_at")
    )
    facts_by_article = {}
    for fact in validated_facts:
        facts_by_article.setdefault(fact.article_id, []).append(fact)

    events = []
    for relation in relations:
        disease = primary_diseases.get(relation.article_id)
        if disease is None:
            continue

        article = relation.article
        article_date = (
            article.published_at.date()
            if article.published_at
            else article.crawled_at.date()
        )
        matching_facts = [
            fact
            for fact in facts_by_article.get(article.id, [])
            if fact.disease_id == disease.id
            and (
                fact.location_id == relation.location_id
                or fact.location_id is None
            )
        ]
        latest_fact = max(
            matching_facts,
            key=lambda fact: (
                fact.event_date or article_date,
                fact.updated_at,
            ),
            default=None,
        )
        events.append(
            {
                "date": article_date,
                "relation": relation,
                "disease": disease,
                "fact": latest_fact,
            }
        )

    if not events:
        return {
            "timeline": [],
            "locations": {},
            "scope": "global",
            "mode": mode,
            "metric": _global_metric_metadata(mode),
        }

    events.sort(key=lambda item: item["date"])
    earliest = events[0]["date"]
    latest = events[-1]["date"]
    buckets = []
    cursor = earliest
    while cursor <= latest:
        buckets.append(cursor)
        cursor += timedelta(days=7)
    if not buckets or buckets[-1] < latest:
        buckets.append(latest)

    locations_meta = {}
    timeline = []
    def country_for(location):
        current = location
        hops = 0
        while current.parent is not None and hops < 6:
            current = current.parent
            hops += 1
        return current

    for bucket_end in buckets:
        window_start = (
            earliest
            if mode == MAP_MODE_CUMULATIVE
            else bucket_end - timedelta(days=ROLLING_WINDOW_DAYS - 1)
        )
        groups = {}
        for event in events:
            if event["date"] < window_start:
                continue
            if event["date"] > bucket_end:
                break

            relation = event["relation"]
            location = relation.location
            country = country_for(location)
            location_id = str(location.id)
            locations_meta.setdefault(
                location_id,
                {
                    "id": location_id,
                    "name": location.name,
                    "display_name": str(location),
                    "country_name": country.name,
                    "country_code": location.country_code,
                    "administrative_level": location.administrative_level,
                    "latitude": float(location.latitude),
                    "longitude": float(location.longitude),
                },
            )
            group = groups.setdefault(
                location_id,
                {
                    "article_ids": set(),
                    "sources": set(),
                    "diseases": set(),
                    "events": [],
                    "latest_fact": None,
                    "trend": ArticleFact.Trend.UNKNOWN,
                },
            )
            article = relation.article
            group["article_ids"].add(str(article.id))
            if article.source_id:
                group["sources"].add(article.source.name)
            group["diseases"].add(event["disease"].name)
            group["events"].append(
                {
                    "title": article.title,
                    "source": article.source.name if article.source_id else "—",
                    "date": event["date"].isoformat(),
                    "url": article.original_url,
                    "disease": event["disease"].name,
                    "has_numeric_fact": event["fact"] is not None,
                }
            )
            fact = event["fact"]
            if fact is not None:
                current_latest = group["latest_fact"]
                fact_date = fact.event_date or event["date"]
                if current_latest is None or fact_date >= current_latest[0]:
                    group["latest_fact"] = (fact_date, fact)
                    group["trend"] = fact.trend

        frame_locations = {}
        for location_id, group in groups.items():
            latest_fact_pair = group["latest_fact"]
            latest_fact = latest_fact_pair[1] if latest_fact_pair else None
            trend = group["trend"]
            if trend == ArticleFact.Trend.NEW_OCCURRENCE:
                severity = "new_occurrence"
            elif trend in {
                ArticleFact.Trend.INCREASING,
                ArticleFact.Trend.SPREADING,
            }:
                severity = "increasing"
            elif latest_fact is not None:
                severity = "quantitative"
            else:
                severity = "qualitative"

            recent_events = sorted(
                group["events"],
                key=lambda item: item["date"],
                reverse=True,
            )[:5]
            frame_locations[location_id] = {
                "article_count": len(group["article_ids"]),
                "source_count": len(group["sources"]),
                "sources": sorted(group["sources"]),
                "diseases": sorted(group["diseases"]),
                "latest_case_count": (
                    latest_fact.case_count if latest_fact else None
                ),
                "latest_death_count": (
                    latest_fact.death_count if latest_fact else None
                ),
                "latest_numeric_date": (
                    latest_fact_pair[0].isoformat()
                    if latest_fact_pair else None
                ),
                "trend": trend,
                "trend_label": ArticleFact.Trend(trend).label,
                "severity": severity,
                "events": recent_events,
            }

        timeline.append(
            {
                "date": bucket_end.isoformat(),
                "period_start": window_start.isoformat(),
                "period_end": bucket_end.isoformat(),
                "locations": frame_locations,
            }
        )

    return {
        "timeline": timeline,
        "locations": locations_meta,
        "scope": "global",
        "mode": mode,
        "metric": _global_metric_metadata(mode),
    }


def _global_metric_metadata(mode: str) -> dict:
    from .spread_map_metrics import (
        MAP_MODE_CUMULATIVE,
        ROLLING_WINDOW_DAYS,
    )

    cumulative = mode == MAP_MODE_CUMULATIVE
    return {
        "id": "validated_foreign_article_locations",
        "label": (
            "Akumulasi Informasi Penyakit Luar Negeri"
            if cumulative
            else "Informasi Penyakit Luar Negeri 14 Hari Terakhir"
        ),
        "short_label": "Status informasi luar negeri",
        "unit": "artikel tervalidasi",
        "normalized": False,
        "reference_year": None,
        "source_name": "Artikel OSINT tervalidasi",
        "source_url": "",
        "window_days": None if cumulative else ROLLING_WINDOW_DAYS,
        "mode": mode,
        "caveat": (
            "Ukuran marker menunjukkan jumlah artikel tervalidasi. Angka "
            "kasus yang ditampilkan adalah angka terbaru, bukan hasil "
            "penjumlahan antarartikel dan bukan statistik resmi."
        ),
    }


@require_role(*Roles.ALL)
def location_detail(
    request: HttpRequest,
    location_id,
) -> HttpResponse:
    """Halaman detail satu lokasi: info dasar, alias, wilayah anak,
    dan riwayat artikel yang menyebutnya (urut waktu, sebagai
    pengganti audit log karena Location tidak punya model riwayat
    formal).
    """
    location = get_object_or_404(
        Location.objects.select_related("parent"),
        id=location_id,
    )

    children = location.children.order_by(
        "administrative_level", "name"
    )

    aliases = location.aliases.order_by("-is_active", "alias")

    from apps.entities.models import ArticleLocation

    mentions = (
        ArticleLocation.objects.filter(location=location)
        .select_related("article", "article__source")
        .order_by("-article__published_at", "-article__crawled_at")
    )

    paginator = Paginator(mentions, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    summary = {
        "total_mentions": mentions.count(),
        "primary_mentions": mentions.filter(is_primary=True).count(),
        "validated_mentions": mentions.filter(
            validation_status=ValidationStatus.VALIDATED
        ).count(),
        "child_count": children.count(),
        "alias_count": aliases.count(),
    }

    context = {
        "page_title": f"Lokasi — {location.name}",
        "active_menu": "geocoding",
        "location": location,
        "children": children,
        "aliases": aliases,
        "page_obj": page_obj,
        "summary": summary,
    }

    return render(
        request,
        "dashboard/location_detail.html",
        context,
    )


def _parse_date_or_none(value: str):
    from django.utils.dateparse import parse_date

    value = (value or "").strip()
    return parse_date(value) if value else None


def _report_archive_queryset(request, report_type: str):
    """Bangun queryset arsip sesuai tipe laporan + filter dari
    querystring. Dipakai bersama oleh halaman & endpoint ekspor
    CSV/PDF supaya hasilnya selalu konsisten.
    """
    from django.db.models import Q as DjangoQ

    from apps.signals.models import Signal
    from apps.assessments.models import (
        EarlyWarning,
        IntelligenceRecommendation,
        SignalAssessment,
    )

    date_from = _parse_date_or_none(request.GET.get("date_from", ""))
    date_to = _parse_date_or_none(request.GET.get("date_to", ""))
    status_filter = request.GET.get("status", "").strip()
    disease_code = request.GET.get("disease", "").strip()
    search_query = request.GET.get("q", "").strip()
    sort_key = request.GET.get("sort", "").strip()

    # Kolom yang boleh dipakai buat sortir per tipe laporan (allowlist,
    # bukan menerima nama field mentah dari querystring langsung --
    # supaya tidak bisa dipakai untuk mengintip field/relasi lain).
    sort_fields = {
        "warning": {
            "title": "title",
            "code": "code",
            "status": "status",
            "level": "level",
            "date": "issued_at",
        },
        "recommendation": {
            "title": "title",
            "code": "code",
            "status": "status",
            "urgency": "urgency",
            "date": "created_at",
        },
        "assessment": {
            "title": "signal__title",
            "status": "status",
            "priority": "recommended_priority",
            "priority_score": "priority_score",
            "confidence_score": "confidence_score",
            "date": "assessed_at",
        },
        "signal": {
            "title": "title",
            "code": "code",
            "status": "status",
            "priority": "priority_level",
            "date": "first_detected_at",
        },
    }

    search_fields = {
        "warning": ["code", "title"],
        "recommendation": ["code", "title"],
        "assessment": ["signal__title"],
        "signal": ["code", "title"],
    }

    if report_type == "warning":
        qs = EarlyWarning.objects.select_related(
            "signal__primary_disease",
            "signal__primary_location",
        )
        date_field = "issued_at"
        disease_field = "signal__primary_disease__code"
        default_sort = "-issued_at"
    elif report_type == "recommendation":
        qs = IntelligenceRecommendation.objects.select_related(
            "signal__primary_disease",
            "signal__primary_location",
        )
        date_field = "created_at"
        disease_field = "signal__primary_disease__code"
        default_sort = "-created_at"
    elif report_type == "assessment":
        qs = SignalAssessment.objects.select_related(
            "signal__primary_disease",
            "signal__primary_location",
        )
        date_field = "assessed_at"
        disease_field = "signal__primary_disease__code"
        default_sort = "-assessed_at"
    else:
        report_type = "signal"
        qs = Signal.objects.select_related(
            "primary_disease",
            "primary_location",
        )
        date_field = "first_detected_at"
        disease_field = "primary_disease__code"
        default_sort = "-first_detected_at"

    if date_from:
        qs = qs.filter(**{f"{date_field}__date__gte": date_from})
    if date_to:
        qs = qs.filter(**{f"{date_field}__date__lte": date_to})
    if status_filter:
        qs = qs.filter(status=status_filter)
    if disease_code:
        qs = qs.filter(**{disease_field: disease_code})

    if search_query:
        fields = search_fields[report_type]
        search_condition = DjangoQ()
        for field in fields:
            search_condition |= DjangoQ(
                **{f"{field}__icontains": search_query}
            )
        qs = qs.filter(search_condition)

    descending = sort_key.startswith("-")
    sort_column = sort_key.lstrip("-")
    allowed = sort_fields[report_type]

    if sort_column in allowed:
        order_field = allowed[sort_column]
        qs = qs.order_by(
            f"-{order_field}" if descending else order_field
        )
    else:
        sort_key = ""
        qs = qs.order_by(default_sort)

    return report_type, qs, sort_key


@require_role(*Roles.ALL)
def report_archive(request: HttpRequest) -> HttpResponse:
    """Halaman "Laporan & Arsip": arsip Sinyal, Early Warning, dan
    Rekomendasi Intelijen yang bisa difilter tanggal/status/penyakit,
    plus ekspor CSV.
    """
    from apps.signals.models import Signal
    from apps.assessments.models import (
        EarlyWarning,
        IntelligenceRecommendation,
        SignalAssessment,
    )

    report_type = request.GET.get("type", "signal").strip()
    if report_type not in (
        "signal",
        "warning",
        "recommendation",
        "assessment",
    ):
        report_type = "signal"

    report_type, records, active_sort = _report_archive_queryset(request, report_type)

    paginator = Paginator(records, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    status_choices = {
        "signal": Signal.Status.choices,
        "warning": EarlyWarning.Status.choices,
        "recommendation": IntelligenceRecommendation.Status.choices,
        "assessment": SignalAssessment.Status.choices,
    }[report_type]

    context = {
        "page_title": "Laporan & Arsip",
        "active_menu": "reports",
        "report_type": report_type,
        "page_obj": page_obj,
        "status_choices": status_choices,
        "diseases": Disease.objects.filter(is_active=True).order_by("name"),
        "selected_status": request.GET.get("status", ""),
        "selected_disease": request.GET.get("disease", ""),
        "search_query": request.GET.get("q", ""),
        "active_sort": active_sort,
        "base_qs": urlencode(
            {
                "type": report_type,
                "status": request.GET.get("status", ""),
                "disease": request.GET.get("disease", ""),
                "q": request.GET.get("q", ""),
                "date_from": request.GET.get("date_from", ""),
                "date_to": request.GET.get("date_to", ""),
            }
        ),
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
    }

    return render(
        request,
        "dashboard/report_archive.html",
        context,
    )


@require_role(*Roles.ALL)
def report_archive_export(request: HttpRequest) -> HttpResponse:
    """Ekspor arsip (dengan filter yang sedang aktif) ke CSV."""
    import csv

    report_type = request.GET.get("type", "signal").strip()
    report_type, records, active_sort = _report_archive_queryset(request, report_type)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="arsip_{report_type}.csv"'
    )

    writer = csv.writer(response)

    if report_type == "warning":
        writer.writerow(
            ["Kode", "Judul", "Level", "Status", "Penyakit", "Lokasi", "Tanggal Terbit"]
        )
        for item in records:
            writer.writerow(
                [
                    item.code,
                    item.title,
                    item.get_level_display(),
                    item.get_status_display(),
                    item.signal.primary_disease.name if item.signal.primary_disease else "",
                    item.signal.primary_location.name if item.signal.primary_location else "",
                    item.issued_at.strftime("%Y-%m-%d %H:%M") if item.issued_at else "",
                ]
            )
    elif report_type == "recommendation":
        writer.writerow(
            ["Kode", "Judul", "Urgensi", "Status", "Penyakit", "Lokasi", "Tanggal Dibuat"]
        )
        for item in records:
            writer.writerow(
                [
                    item.code,
                    item.title,
                    item.get_urgency_display(),
                    item.get_status_display(),
                    item.signal.primary_disease.name if item.signal.primary_disease else "",
                    item.signal.primary_location.name if item.signal.primary_location else "",
                    item.created_at.strftime("%Y-%m-%d %H:%M") if item.created_at else "",
                ]
            )
    elif report_type == "assessment":
        writer.writerow(
            ["Sinyal", "Status", "Prioritas Rekomendasi", "Skor Prioritas", "Skor Keyakinan", "Penyakit", "Lokasi", "Tanggal Dinilai"]
        )
        for item in records:
            writer.writerow(
                [
                    item.signal.title,
                    item.get_status_display(),
                    item.get_recommended_priority_display(),
                    item.priority_score,
                    item.confidence_score,
                    item.signal.primary_disease.name if item.signal.primary_disease else "",
                    item.signal.primary_location.name if item.signal.primary_location else "",
                    item.assessed_at.strftime("%Y-%m-%d %H:%M") if item.assessed_at else "",
                ]
            )
    else:
        writer.writerow(
            ["Kode", "Judul", "Prioritas", "Status", "Penyakit", "Lokasi", "Terdeteksi"]
        )
        for item in records:
            writer.writerow(
                [
                    item.code,
                    item.title,
                    item.get_priority_level_display(),
                    item.get_status_display(),
                    item.primary_disease.name if item.primary_disease else "",
                    item.primary_location.name if item.primary_location else "",
                    item.first_detected_at.strftime("%Y-%m-%d %H:%M") if item.first_detected_at else "",
                ]
            )

    return response


REPORT_TYPE_LABELS = {
    "signal": "Sinyal Intelijen",
    "warning": "Early Warning",
    "recommendation": "Rekomendasi Intelijen",
    "assessment": "Assessment Ancaman",
}


@require_role(*Roles.ALL)
def report_archive_export_pdf(request: HttpRequest) -> HttpResponse:
    """Ekspor arsip (dengan filter yang sedang aktif) ke PDF -- format
    laporan resmi dengan kop, judul, dan tabel, cocok untuk
    didistribusikan ke pemangku kepentingan.
    """
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    report_type = request.GET.get("type", "signal").strip()
    report_type, records, active_sort = _report_archive_queryset(request, report_type)
    label = REPORT_TYPE_LABELS.get(report_type, "Arsip")

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("MEDINTEL OSINT", styles["Title"]))
    story.append(
        Paragraph(f"Arsip {label}", styles["Heading2"])
    )

    generated_at = timezone.localtime(timezone.now()).strftime(
        "%d %B %Y %H:%M"
    )
    story.append(
        Paragraph(
            f"Dicetak: {generated_at} WIB &middot; Total data: {records.count()}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.6 * cm))

    if report_type == "warning":
        header = ["Kode", "Judul", "Level", "Status", "Penyakit", "Lokasi", "Tanggal"]
        rows = [
            [
                item.code,
                item.title,
                item.get_level_display(),
                item.get_status_display(),
                item.signal.primary_disease.name if item.signal.primary_disease else "-",
                item.signal.primary_location.name if item.signal.primary_location else "-",
                item.issued_at.strftime("%d-%m-%Y") if item.issued_at else "-",
            ]
            for item in records
        ]
    elif report_type == "recommendation":
        header = ["Kode", "Judul", "Urgensi", "Status", "Penyakit", "Lokasi", "Tanggal"]
        rows = [
            [
                item.code,
                item.title,
                item.get_urgency_display(),
                item.get_status_display(),
                item.signal.primary_disease.name if item.signal.primary_disease else "-",
                item.signal.primary_location.name if item.signal.primary_location else "-",
                item.created_at.strftime("%d-%m-%Y") if item.created_at else "-",
            ]
            for item in records
        ]
    elif report_type == "assessment":
        header = ["Sinyal", "Prioritas", "Skor Prioritas", "Skor Keyakinan", "Status", "Tanggal"]
        rows = [
            [
                item.signal.title,
                item.get_recommended_priority_display(),
                f"{item.priority_score:.1f}",
                f"{item.confidence_score:.1f}",
                item.get_status_display(),
                item.assessed_at.strftime("%d-%m-%Y") if item.assessed_at else "-",
            ]
            for item in records
        ]
    else:
        header = ["Kode", "Judul", "Prioritas", "Status", "Penyakit", "Lokasi", "Terdeteksi"]
        rows = [
            [
                item.code,
                item.title,
                item.get_priority_level_display(),
                item.get_status_display(),
                item.primary_disease.name if item.primary_disease else "-",
                item.primary_location.name if item.primary_location else "-",
                item.first_detected_at.strftime("%d-%m-%Y") if item.first_detected_at else "-",
            ]
            for item in records
        ]

    # Bungkus teks panjang (judul) supaya tidak meluber keluar tabel.
    body_style = styles["Normal"]
    body_style.fontSize = 8
    wrapped_rows = [
        [
            Paragraph(str(cell), body_style) if i == 1 else str(cell)
            for i, cell in enumerate(row)
        ]
        for row in rows
    ]

    table_data = [header] + wrapped_rows

    if len(rows) == 0:
        story.append(
            Paragraph(
                "Tidak ada data yang cocok dengan filter ini.",
                styles["Normal"],
            )
        )
    else:
        table = Table(table_data, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a56db")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, 0), 9),
                    ("FONTSIZE", (0, 1), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)

    doc.build(story)
    buffer.seek(0)

    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="arsip_{report_type}.pdf"'
    )
    return response


@require_role(*Roles.ALL)
def periodic_summary(request: HttpRequest) -> HttpResponse:
    """Ringkasan periodik: rekap seluruh aktivitas (sinyal baru,
    eskalasi, early warning terbit, rekomendasi dibuat) dalam satu
    rentang waktu -- untuk briefing rutin, beda dari Dashboard
    Eksekutif yang menampilkan kondisi TERKINI (bukan retrospektif
    per periode).
    """
    from datetime import timedelta
    from django.db.models import Count
    from django.utils import timezone

    from apps.signals.models import Signal, SignalHistory
    from apps.assessments.models import (
        EarlyWarning,
        IntelligenceRecommendation,
    )

    preset = request.GET.get("preset", "30hari").strip()
    today = timezone.localdate()

    if preset == "minggu_ini":
        date_from = today - timedelta(days=today.weekday())
        date_to = today
    elif preset == "bulan_ini":
        date_from = today.replace(day=1)
        date_to = today
    elif preset == "custom":
        date_from = _parse_date_or_none(
            request.GET.get("date_from", "")
        ) or (today - timedelta(days=30))
        date_to = _parse_date_or_none(
            request.GET.get("date_to", "")
        ) or today
    else:
        preset = "30hari"
        date_from = today - timedelta(days=30)
        date_to = today

    new_signals = Signal.objects.filter(
        first_detected_at__date__range=[date_from, date_to],
    )

    escalations = SignalHistory.objects.filter(
        to_status=Signal.Status.ESCALATED,
        changed_at__date__range=[date_from, date_to],
    )

    warnings = EarlyWarning.objects.filter(
        issued_at__date__range=[date_from, date_to],
    ).select_related("signal__primary_disease", "signal__primary_location")

    recommendations = IntelligenceRecommendation.objects.filter(
        created_at__date__range=[date_from, date_to],
    )

    warnings_by_level = {
        row["level"]: row["count"]
        for row in warnings.values("level").annotate(count=Count("id"))
    }
    level_labels = dict(EarlyWarning.Level.choices)

    recommendations_by_status = {
        row["status"]: row["count"]
        for row in recommendations.values("status").annotate(
            count=Count("id")
        )
    }
    recommendation_status_labels = dict(
        IntelligenceRecommendation.Status.choices
    )

    top_diseases = (
        new_signals.exclude(primary_disease__isnull=True)
        .values("primary_disease__name")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    top_locations = (
        new_signals.exclude(primary_location__isnull=True)
        .values("primary_location__name")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    # Bucket mingguan untuk grafik tren -- jumlah sinyal baru & early
    # warning per minggu sepanjang periode yang dipilih. Dibatasi
    # maksimal 52 bucket (~1 tahun) supaya rentang custom yang sangat
    # panjang tidak memicu ratusan query kecil sekaligus.
    week_labels = []
    signal_weekly_counts = []
    warning_weekly_counts = []

    cursor = date_from
    bucket_guard = 0
    while cursor <= date_to and bucket_guard < 52:
        week_end = min(cursor + timedelta(days=6), date_to)

        week_labels.append(cursor.strftime("%d %b"))
        signal_weekly_counts.append(
            new_signals.filter(
                first_detected_at__date__range=[cursor, week_end],
            ).count()
        )
        warning_weekly_counts.append(
            warnings.filter(
                issued_at__date__range=[cursor, week_end],
            ).count()
        )

        cursor = week_end + timedelta(days=1)
        bucket_guard += 1

    trend_chart_data = {
        "labels": week_labels,
        "signals": signal_weekly_counts,
        "warnings": warning_weekly_counts,
    }

    disease_chart_data = {
        "labels": [row["primary_disease__name"] for row in top_diseases],
        "counts": [row["count"] for row in top_diseases],
    }

    location_chart_data = {
        "labels": [row["primary_location__name"] for row in top_locations],
        "counts": [row["count"] for row in top_locations],
    }

    context = {
        "page_title": "Ringkasan Periodik",
        "active_menu": "reports",
        "preset": preset,
        "date_from": date_from,
        "date_to": date_to,
        "summary": {
            "new_signals_count": new_signals.count(),
            "escalations_count": escalations.count(),
            "warnings_count": warnings.count(),
            "recommendations_count": recommendations.count(),
        },
        "warnings_by_level": [
            {
                "level": level,
                "label": level_labels.get(level, level),
                "count": count,
            }
            for level, count in warnings_by_level.items()
        ],
        "recommendations_by_status": [
            {
                "status": status,
                "label": recommendation_status_labels.get(status, status),
                "count": count,
            }
            for status, count in recommendations_by_status.items()
        ],
        "top_diseases": top_diseases,
        "top_locations": top_locations,
        "warnings_list": warnings.order_by("-level", "-issued_at")[:15],
        "trend_chart_data": trend_chart_data,
        "disease_chart_data": disease_chart_data,
        "location_chart_data": location_chart_data,
    }

    return render(
        request,
        "dashboard/periodic_summary.html",
        context,
    )


def _entity_extraction_incomplete_queryset():
    """Artikel yang masih kekurangan penyakit, lokasi, atau fakta numerik."""
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
        Article.objects.exclude(
            processing_status__in=[
                Article.ProcessingStatus.VALIDATED,
                Article.ProcessingStatus.REJECTED,
            ],
        )
        .annotate(
            extraction_has_disease=Exists(disease_exists),
            extraction_has_location=Exists(location_exists),
            extraction_has_numeric_fact=Exists(numeric_fact_exists),
        )
        .filter(
            Q(extraction_has_disease=False)
            | Q(extraction_has_location=False)
            | Q(extraction_has_numeric_fact=False)
        )
    )


def _entity_extraction_summary() -> dict:
    return {
        "total_articles": Article.objects.count(),
        "pending_count": _entity_extraction_incomplete_queryset().count(),
        "with_disease": Article.objects.filter(
            diseases__isnull=False,
        ).distinct().count(),
        "with_location": Article.objects.filter(
            locations__isnull=False,
        ).distinct().count(),
        "with_fact": Article.objects.filter(
            facts__isnull=False,
        ).distinct().count(),
    }


@require_role(*Roles.ALL)
def entity_extraction_dashboard(request: HttpRequest) -> HttpResponse:
    """Halaman "Ekstraksi Entitas": cakupan hasil ekstraksi
    penyakit/lokasi/fakta di seluruh artikel, plus tombol untuk
    memicu pemrosesan artikel yang belum/tidak lengkap diekstraksi
    langsung dari web (tanpa perlu buka terminal).
    """
    from apps.entities.models import (
        ArticleDisease,
        ArticleFact,
        ArticleLocation,
        ExtractionMethod,
    )

    status_counts = dict(
        Article.objects.values("processing_status")
        .annotate(count=Count("id"))
        .values_list("processing_status", "count")
    )
    status_labels = dict(Article.ProcessingStatus.choices)

    method_counts = dict(
        ArticleDisease.objects.values("extraction_method")
        .annotate(count=Count("id"))
        .values_list("extraction_method", "count")
    )
    method_labels = dict(ExtractionMethod.choices)

    recent_processed = Article.objects.filter(
        processing_status__in=[
            Article.ProcessingStatus.PROCESSED,
            Article.ProcessingStatus.FAILED,
        ],
    ).select_related("source").order_by("-updated_at")[:15]

    context = {
        "page_title": "Ekstraksi Entitas",
        "active_menu": "entity_extraction",
        "summary": _entity_extraction_summary(),
        "status_breakdown": [
            {
                "label": status_labels.get(status, status),
                "count": count,
            }
            for status, count in status_counts.items()
        ],
        "method_breakdown": [
            {
                "label": method_labels.get(method, method),
                "count": count,
            }
            for method, count in method_counts.items()
        ],
        "recent_processed": recent_processed,
    }

    return render(
        request,
        "dashboard/entity_extraction_dashboard.html",
        context,
    )


@require_role(Roles.ADMIN, Roles.ANALYST)
@require_POST
def entity_extraction_run(request: HttpRequest) -> HttpResponse:
    """Picu pemrosesan (ekstraksi) artikel yang belum lengkap,
    berjalan di background thread supaya tidak memblokir request --
    pola sama seperti "Jalankan Crawler".
    """
    import threading
    import uuid

    from django.core.cache import cache
    from django.db import close_old_connections

    force = request.POST.get("force") == "1"
    limit_raw = request.POST.get("limit", "").strip()

    try:
        limit = int(limit_raw) if limit_raw else 200
    except ValueError:
        limit = 200
    limit = max(1, min(limit, 2000))

    if force:
        articles_qs = Article.objects.all()
    else:
        articles_qs = _entity_extraction_incomplete_queryset()

    article_ids = list(
        articles_qs.order_by("-created_at").values_list(
            "id", flat=True
        )[:limit]
    )

    if not article_ids:
        messages.warning(
            request,
            "Tidak ada artikel yang perlu diproses.",
        )
        return redirect("dashboard:entity-extraction")

    batch_id = uuid.uuid4().hex
    cache_key = f"entity-extraction-batch:{batch_id}"
    batch_status = {
        "state": "running",
        "total": len(article_ids),
        "processed": 0,
        "failed": 0,
    }
    cache.set(cache_key, batch_status, timeout=3600)

    def _run() -> None:
        close_old_connections()

        from apps.entities.services import process_article_full

        for article_id in article_ids:
            try:
                article = Article.objects.get(id=article_id)
                process_article_full(article)
            except Exception:
                batch_status["failed"] += 1
                logger.exception(
                    "Ekstraksi manual gagal untuk artikel=%s",
                    article_id,
                )
            finally:
                batch_status["processed"] += 1
                cache.set(cache_key, dict(batch_status), timeout=3600)

        batch_status["state"] = "completed"
        cache.set(cache_key, dict(batch_status), timeout=3600)
        close_old_connections()

    thread = threading.Thread(
        target=_run,
        name="entity-extraction-batch",
        daemon=True,
    )
    thread.start()

    target_url = reverse("dashboard:entity-extraction")
    return redirect(f"{target_url}?batch={batch_id}")


@require_role(*Roles.ALL)
def entity_extraction_status(request: HttpRequest) -> JsonResponse:
    """Snapshot progres batch dan ringkasan ekstraksi untuk polling UI."""
    from django.core.cache import cache

    batch_id = request.GET.get("batch", "").strip()
    batch = None
    if batch_id:
        batch = cache.get(f"entity-extraction-batch:{batch_id}")

    status_counts = dict(
        Article.objects.values("processing_status")
        .annotate(count=Count("id"))
        .values_list("processing_status", "count")
    )
    status_labels = dict(Article.ProcessingStatus.choices)
    from apps.entities.models import ExtractionMethod

    method_counts = dict(
        ArticleDisease.objects.values("extraction_method")
        .annotate(count=Count("id"))
        .values_list("extraction_method", "count")
    )
    method_labels = dict(ExtractionMethod.choices)

    recent_processed = (
        Article.objects.filter(
            processing_status__in=[
                Article.ProcessingStatus.PROCESSED,
                Article.ProcessingStatus.FAILED,
            ],
        )
        .select_related("source")
        .order_by("-updated_at")[:15]
    )

    return JsonResponse(
        {
            "batch": batch,
            "summary": _entity_extraction_summary(),
            "status_breakdown": [
                {
                    "label": status_labels.get(status, status),
                    "count": count,
                }
                for status, count in status_counts.items()
            ],
            "method_breakdown": [
                {
                    "label": method_labels.get(method, method),
                    "count": count,
                }
                for method, count in method_counts.items()
            ],
            "recent_processed": [
                {
                    "title": article.title,
                    "source": article.source.name,
                    "status": article.processing_status,
                    "status_label": article.get_processing_status_display(),
                    "updated_at": timezone.localtime(
                        article.updated_at
                    ).strftime("%d %b %Y %H:%M"),
                    "detail_url": reverse(
                        "dashboard:article-extraction-detail",
                        args=[article.id],
                    ),
                }
                for article in recent_processed
            ],
        }
    )


def _highlight_article_content(article: Article) -> str:
    """Bangun HTML konten artikel dengan span penyakit/lokasi/fakta
    di-highlight warna berbeda, untuk halaman detail ekstraksi
    per-artikel.

    Catatan jujur soal keterbatasan: penyakit & lokasi punya
    `mention_text` (span pendek presisi), jadi bisa di-highlight tepat
    di kata/frasanya. Fakta numerik (jumlah kasus/meninggal/tanggal)
    TIDAK punya span kata per-kata tersimpan di database -- yang ada
    cuma `fact_text` (satu kalimat pendukung penuh). Jadi untuk fakta,
    yang di-highlight adalah kalimat pendukungnya, bukan angkanya
    secara spesifik.
    """
    from django.utils.html import escape

    content = article.content_text or ""

    # (start, end, css_class, label) -- dikumpulkan dari semua sumber,
    # lalu diurutkan dan di-render tanpa overlap (yang duluan menang).
    spans = []

    for relation in article.article_diseases.all():
        mention = relation.mention_text
        if not mention:
            continue
        start = content.lower().find(mention.lower())
        if start >= 0:
            spans.append(
                (start, start + len(mention), "medintel-hl-disease", "Penyakit")
            )

    for relation in article.article_locations.all():
        mention = relation.mention_text
        if not mention:
            continue
        start = content.lower().find(mention.lower())
        if start >= 0:
            spans.append(
                (start, start + len(mention), "medintel-hl-location", "Lokasi")
            )

    for fact in article.facts.all():
        snippet = (fact.fact_text or "").strip()
        if not snippet:
            continue
        start = content.find(snippet)
        if start >= 0:
            spans.append(
                (start, start + len(snippet), "medintel-hl-fact", "Fakta")
            )

    spans.sort(key=lambda item: item[0])

    rendered = []
    cursor = 0

    for start, end, css_class, label in spans:
        if start < cursor:
            # Tumpang tindih dengan span sebelumnya -- lewati supaya
            # tag <mark> tidak bersarang/rusak.
            continue

        rendered.append(escape(content[cursor:start]))
        rendered.append(
            f'<mark class="{css_class}" title="{label}">'
            f"{escape(content[start:end])}</mark>"
        )
        cursor = end

    rendered.append(escape(content[cursor:]))

    return "".join(rendered)


@require_role(*Roles.ALL)
def article_extraction_detail(
    request: HttpRequest,
    article_id,
) -> HttpResponse:
    """Halaman "Ekstraksi Entitas" per-artikel: teks artikel dengan
    penyakit/lokasi/fakta di-highlight langsung di konteksnya, plus
    tabel evidence dan skor keyakinan -- pelengkap dashboard agregat
    yang sudah ada di /ekstraksi-entitas/.
    """
    article = get_object_or_404(
        Article.objects.select_related("source"),
        id=article_id,
    )

    diseases = article.article_diseases.select_related("disease").all()
    locations = article.article_locations.select_related("location").all()
    facts = article.facts.select_related("disease", "location").all()

    evidence_rows = []

    for relation in diseases:
        evidence_rows.append(
            {
                "type": "Penyakit",
                "css_class": "medintel-hl-disease",
                "value": relation.disease.name,
                "confidence": relation.confidence_score,
                "snippet": relation.mention_text,
            }
        )

    for relation in locations:
        evidence_rows.append(
            {
                "type": "Lokasi",
                "css_class": "medintel-hl-location",
                "value": relation.location.name,
                "confidence": relation.confidence_score,
                "snippet": relation.mention_text,
            }
        )

    for fact in facts:
        if fact.case_count is not None:
            evidence_rows.append(
                {
                    "type": "Jumlah Kasus",
                    "css_class": "medintel-hl-fact",
                    "value": f"{fact.case_count} kasus",
                    "confidence": fact.confidence_score,
                    "snippet": fact.fact_text,
                }
            )
        if fact.death_count is not None:
            evidence_rows.append(
                {
                    "type": "Jumlah Meninggal",
                    "css_class": "medintel-hl-fact",
                    "value": f"{fact.death_count} kasus",
                    "confidence": fact.confidence_score,
                    "snippet": fact.fact_text,
                }
            )
        if fact.event_date is not None:
            evidence_rows.append(
                {
                    "type": "Tanggal Kejadian",
                    "css_class": "medintel-hl-fact",
                    "value": fact.event_date.strftime("%d %b %Y"),
                    "confidence": fact.confidence_score,
                    "snippet": fact.fact_text,
                }
            )

    confidence_values = [
        row["confidence"]
        for row in evidence_rows
        if row["confidence"] is not None
    ]
    overall_confidence = (
        round(sum(confidence_values) / len(confidence_values) * 100)
        if confidence_values
        else None
    )

    context = {
        "page_title": f"Ekstraksi Entitas — {article.title}",
        "active_menu": "entity_extraction",
        "article": article,
        "highlighted_content": _highlight_article_content(article),
        "evidence_rows": evidence_rows,
        "overall_confidence": overall_confidence,
        "disease_count": diseases.count(),
        "location_count": locations.count(),
        "fact_count": facts.count(),
    }

    return render(
        request,
        "dashboard/article_extraction_detail.html",
        context,
    )


@require_role(*Roles.ALL)
def national_risk_dashboard(request: HttpRequest) -> HttpResponse:
    """Dashboard skor risiko nasional (agregat lintas wilayah), beda
    dari Assessment Ancaman (workspace kerja per-sinyal satu-satu) --
    ini pandangan "burung" dari seluruh assessment yang ada, dengan
    komposisi faktor risiko berdasarkan BOBOT ASLI formula
    calculate_priority_score (Impact 30%, Urgency 25%, Geographic
    Scope 15%, Development Speed 15%, Vulnerability 15%), bukan
    kategori karangan.
    """
    from datetime import timedelta
    from django.db.models import Avg, Count
    from django.utils import timezone

    from apps.assessments.models import SignalAssessment

    current_assessments = SignalAssessment.objects.filter(
        is_current=True,
    ).select_related(
        "signal__primary_location",
        "signal__primary_disease",
    )

    total_analyzed = SignalAssessment.objects.count()

    national_score = current_assessments.aggregate(
        avg=Avg("priority_score"),
    )["avg"]
    national_score_display = (
        round(national_score * 100) if national_score is not None else 0
    )

    HIGH_RISK_THRESHOLD = 0.7

    now = timezone.now()
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    this_week_avg = SignalAssessment.objects.filter(
        assessed_at__gte=week_ago,
    ).aggregate(avg=Avg("priority_score"))["avg"] or 0

    last_week_avg = SignalAssessment.objects.filter(
        assessed_at__gte=two_weeks_ago,
        assessed_at__lt=week_ago,
    ).aggregate(avg=Avg("priority_score"))["avg"] or 0

    score_trend = round((this_week_avg - last_week_avg) * 100)

    # Peringkat wilayah: rata-rata priority_score per primary_location,
    # dari assessment yang masih current saja.
    location_rankings = (
        current_assessments.exclude(
            signal__primary_location__isnull=True,
        )
        .values("signal__primary_location__name")
        .annotate(
            avg_score=Avg("priority_score"),
            signal_count=Count("id"),
        )
        .order_by("-avg_score")[:10]
    )

    def _level_for_score(score):
        if score >= HIGH_RISK_THRESHOLD:
            return "Tinggi", "bg-danger-lt"
        if score >= 0.4:
            return "Sedang", "bg-warning-lt"
        return "Rendah", "bg-success-lt"

    location_rows = []
    for row in location_rankings:
        level, badge_class = _level_for_score(row["avg_score"])
        location_rows.append(
            {
                "name": row["signal__primary_location__name"],
                "score": round(row["avg_score"] * 100),
                "level": level,
                "badge_class": badge_class,
                "signal_count": row["signal_count"],
            }
        )

    high_risk_count = sum(
        1
        for row in location_rankings
        if row["avg_score"] >= HIGH_RISK_THRESHOLD
    )

    # Komposisi faktor risiko -- rata-rata tiap komponen (dinormalisasi
    # 0-1) DIKALI bobot resminya di calculate_priority_score, supaya
    # totalnya proporsional terhadap kontribusi asli ke priority_score.
    component_averages = current_assessments.aggregate(
        urgency=Avg("urgency_score"),
        impact=Avg("impact_score"),
        geographic_scope=Avg("geographic_scope_score"),
        development_speed=Avg("development_speed_score"),
        vulnerability=Avg("vulnerability_score"),
    )

    WEIGHTS = {
        "urgency": 0.25,
        "impact": 0.30,
        "geographic_scope": 0.15,
        "development_speed": 0.15,
        "vulnerability": 0.15,
    }
    LABELS = {
        "urgency": "Urgensi",
        "impact": "Dampak",
        "geographic_scope": "Cakupan Geografis",
        "development_speed": "Kecepatan Perkembangan",
        "vulnerability": "Kerentanan",
    }

    weighted_contributions = {}
    for key, avg_1_5 in component_averages.items():
        normalized = (avg_1_5 or 0) / 5.0
        weighted_contributions[key] = normalized * WEIGHTS[key]

    total_weighted = sum(weighted_contributions.values()) or 1

    risk_composition = [
        {
            "label": LABELS[key],
            "percentage": round(
                (value / total_weighted) * 100
            ),
        }
        for key, value in weighted_contributions.items()
    ]

    # Interpretasi & saran -- narasi TEMPLATE dari data nyata (bukan
    # teks yang dikarang), supaya tetap bisa dipertanggungjawabkan
    # sebagai output sistem, bukan AI generatif.
    top_location = location_rows[0] if location_rows else None
    dominant_factor = (
        max(risk_composition, key=lambda item: item["percentage"])
        if risk_composition
        else None
    )

    context = {
        "page_title": "Skor Risiko Nasional",
        "active_menu": "assessment",
        "national_score": national_score_display,
        "high_risk_count": high_risk_count,
        "score_trend": score_trend,
        "total_analyzed": total_analyzed,
        "location_rows": location_rows,
        "risk_composition": risk_composition,
        "top_location": top_location,
        "dominant_factor": dominant_factor,
    }

    return render(
        request,
        "dashboard/national_risk_dashboard.html",
        context,
    )


@require_role(*Roles.ALL)
def recommendation_regional_dashboard(request: HttpRequest) -> HttpResponse:
    """Dashboard "Rekomendasi per Wilayah" -- pandangan agregat lintas
    wilayah + tracking distribusi ke instansi tujuan (target_unit),
    pelengkap workspace Rekomendasi Intelijen yang sifatnya kerja
    satu-per-satu.
    """
    from django.db.models import Count, Max

    from apps.assessments.models import IntelligenceRecommendation

    current_recs = IntelligenceRecommendation.objects.filter(
        is_current=True,
    ).select_related(
        "signal__primary_location",
        "signal__primary_disease",
    )

    active_statuses = [
        IntelligenceRecommendation.Status.APPROVED,
        IntelligenceRecommendation.Status.IN_PROGRESS,
    ]

    summary = {
        "active_count": current_recs.filter(
            status__in=active_statuses,
        ).count(),
        "high_priority_count": current_recs.filter(
            urgency__in=[
                IntelligenceRecommendation.Urgency.URGENT,
                IntelligenceRecommendation.Urgency.IMMEDIATE,
            ],
        ).count(),
        "in_progress_count": current_recs.filter(
            status=IntelligenceRecommendation.Status.IN_PROGRESS,
        ).count(),
        "target_unit_count": current_recs.exclude(
            target_unit="",
        ).values("target_unit").distinct().count(),
    }

    priority_list = current_recs.filter(
        urgency__in=[
            IntelligenceRecommendation.Urgency.URGENT,
            IntelligenceRecommendation.Urgency.IMMEDIATE,
        ],
    ).order_by("-created_at")[:10]

    # Kelompokkan per wilayah -- ambil beberapa rekomendasi teratas
    # per wilayah untuk ditampilkan sebagai kartu ringkas.
    regional_groups = {}
    for rec in current_recs.exclude(
        signal__primary_location__isnull=True,
    ).order_by("-created_at"):
        location_name = rec.signal.primary_location.name
        if location_name not in regional_groups:
            regional_groups[location_name] = {
                "location": location_name,
                "disease": (
                    rec.signal.primary_disease.name
                    if rec.signal.primary_disease
                    else None
                ),
                "issue": rec.situation_summary,
                "recommendation": rec.recommended_action,
                "urgency": rec.get_urgency_display(),
                "urgency_value": rec.urgency,
            }

    URGENCY_ORDER = {"immediate": 0, "urgent": 1, "priority": 2, "routine": 3}
    regional_cards = sorted(
        regional_groups.values(),
        key=lambda item: URGENCY_ORDER.get(item["urgency_value"], 9),
    )[:6]

    # Tindak Lanjut & Distribusi -- agregasi per instansi tujuan
    # (target_unit), status TERBARU dan kapan terakhir diperbarui.
    distribution_rows = (
        current_recs.exclude(target_unit="")
        .values("target_unit")
        .annotate(
            count=Count("id"),
            last_updated=Max("updated_at"),
        )
        .order_by("-last_updated")[:10]
    )

    context = {
        "page_title": "Rekomendasi per Wilayah",
        "active_menu": "recommendations",
        "summary": summary,
        "priority_list": priority_list,
        "regional_cards": regional_cards,
        "distribution_rows": distribution_rows,
    }

    return render(
        request,
        "dashboard/recommendation_regional_dashboard.html",
        context,
    )


@require_role(*Roles.ALL)
def report_document_list(request: HttpRequest) -> HttpResponse:
    """Daftar laporan resmi format nota dinas (Kepada/Dari/Tembusan/
    Hal/Nilai + Indikasi/Analisis/Dampak/Upaya/Saran Tindak).
    """
    from apps.assessments.models import IntelligenceReport

    reports = IntelligenceReport.objects.select_related(
        "created_by",
    ).prefetch_related("sections")

    status_filter = request.GET.get("status", "").strip()
    valid_statuses = {
        value for value, _label in IntelligenceReport.Status.choices
    }
    if status_filter in valid_statuses:
        reports = reports.filter(status=status_filter)

    paginator = Paginator(reports, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_title": "Laporan Dokumen",
        "active_menu": "reports",
        "page_obj": page_obj,
        "status_choices": IntelligenceReport.Status.choices,
        "selected_status": status_filter,
    }

    return render(
        request,
        "dashboard/report_document_list.html",
        context,
    )


@require_role(Roles.ADMIN, Roles.ANALYST, Roles.REVIEWER)
def report_document_create(request: HttpRequest) -> HttpResponse:
    """Buat laporan baru: pilih penyakit yang mau dibahas, sistem
    langsung bangkitkan draft tiap poin dari data yang ada (BUKAN
    hasil AI generatif -- pengisian template dari data terstruktur,
    lihat apps/assessments/services/report_generation.py).
    """
    from apps.assessments.models import (
        IntelligenceReport,
        IntelligenceReportSection,
    )
    from apps.assessments.services.report_generation import (
        generate_section_draft,
    )

    if request.method == "POST":
        disease_ids = request.POST.getlist("diseases")

        if not disease_ids:
            messages.error(
                request,
                "Pilih minimal satu penyakit untuk dibahas di laporan.",
            )
            return redirect("dashboard:report-document-create")

        report_date = (
            _parse_date_or_none(request.POST.get("report_date", ""))
            or timezone.localdate()
        )

        report = IntelligenceReport.objects.create(
            kepada=request.POST.get("kepada", "Yth. Pimpinan").strip(),
            dari=request.POST.get("dari", "").strip(),
            tembusan=request.POST.get("tembusan", "").strip(),
            nilai=request.POST.get("nilai", "").strip(),
            report_date=report_date,
            created_by=(
                request.user if request.user.is_authenticated else None
            ),
        )

        diseases = Disease.objects.filter(id__in=disease_ids)

        for index, disease in enumerate(diseases):
            draft = generate_section_draft(disease=disease)

            section = IntelligenceReportSection.objects.create(
                report=report,
                order=index,
                disease=disease,
                indikasi_text=draft.indikasi_text,
                analisis_text=draft.analisis_text,
                dampak_text=draft.dampak_text,
                upaya_text=draft.upaya_text,
                saran_tindak_text=draft.saran_tindak_text,
            )
            if draft.source_article_ids:
                section.source_articles.set(draft.source_article_ids)

        messages.success(
            request,
            (
                "Draft laporan berhasil dibangkitkan. Tinjau dan "
                "sunting tiap bagian sebelum difinalkan."
            ),
        )

        return redirect(
            "dashboard:report-document-edit",
            report_id=report.id,
        )

    diseases = Disease.objects.filter(
        is_active=True,
    ).order_by("name")

    context = {
        "page_title": "Buat Laporan Baru",
        "active_menu": "reports",
        "diseases": diseases,
        "default_date": timezone.localdate(),
    }

    return render(
        request,
        "dashboard/report_document_create.html",
        context,
    )


@require_role(*Roles.ALL)
def report_document_edit(
    request: HttpRequest,
    report_id,
) -> HttpResponse:
    """Tinjau & sunting draft laporan per bagian, ubah status
    (Draft/Final/Didistribusikan/Arsip)."""
    from apps.assessments.models import IntelligenceReport

    report = get_object_or_404(
        IntelligenceReport.objects.prefetch_related(
            "sections__disease",
            "sections__source_articles__source",
        ),
        id=report_id,
    )

    if request.method == "POST":
        if not has_role(request.user, *Roles.CONTRIBUTORS):
            raise PermissionDenied(
                "Peran Viewer tidak dapat mengubah laporan."
            )

        report.kepada = request.POST.get("kepada", report.kepada).strip()
        report.dari = request.POST.get("dari", "").strip()
        report.tembusan = request.POST.get("tembusan", "").strip()
        report.hal = request.POST.get("hal", "").strip()
        report.nilai = request.POST.get("nilai", "").strip()
        report.signature_block = request.POST.get(
            "signature_block", ""
        ).strip()

        new_status = request.POST.get("status", "").strip()
        valid_statuses = {
            value for value, _label in IntelligenceReport.Status.choices
        }
        if new_status in valid_statuses:
            report.status = new_status

        report.save()

        for section in report.sections.all():
            prefix = f"section_{section.id}_"
            section.indikasi_text = request.POST.get(
                f"{prefix}indikasi", section.indikasi_text
            )
            section.analisis_text = request.POST.get(
                f"{prefix}analisis", section.analisis_text
            )
            section.dampak_text = request.POST.get(
                f"{prefix}dampak", section.dampak_text
            )
            section.upaya_text = request.POST.get(
                f"{prefix}upaya", section.upaya_text
            )
            section.saran_tindak_text = request.POST.get(
                f"{prefix}saran_tindak", section.saran_tindak_text
            )
            section.save()

        messages.success(request, "Laporan berhasil disimpan.")

        return redirect(
            "dashboard:report-document-edit",
            report_id=report.id,
        )

    context = {
        "page_title": f"Edit Laporan — {report.report_date}",
        "active_menu": "reports",
        "report": report,
        "status_choices": IntelligenceReport.Status.choices,
    }

    return render(
        request,
        "dashboard/report_document_edit.html",
        context,
    )


@require_role(*Roles.ALL)
def report_document_export_pdf(
    request: HttpRequest,
    report_id,
) -> HttpResponse:
    """Ekspor laporan ke PDF format nota dinas -- header Kepada/Dari/
    Tembusan/Hal/Nilai, lalu bagian I. INDIKASI s.d. V. SARAN TINDAK
    dengan sub-poin alfabet per topik, dan footer tanda tangan.
    """
    from io import BytesIO

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.lib import colors

    from apps.assessments.models import IntelligenceReport

    report = get_object_or_404(
        IntelligenceReport.objects.prefetch_related(
            "sections__disease",
        ),
        id=report_id,
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
    )

    styles = getSampleStyleSheet()

    body_style = ParagraphStyle(
        "MedintelBody",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=11,
        leading=16,
        spaceAfter=8,
        alignment=4,  # justify
    )
    header_label_style = ParagraphStyle(
        "MedintelHeaderLabel",
        parent=styles["Normal"],
        fontName="Times-Italic",
        fontSize=11,
        leading=15,
    )
    section_heading_style = ParagraphStyle(
        "MedintelSectionHeading",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=11,
        leading=15,
        spaceBefore=10,
        spaceAfter=6,
    )
    point_label_style = ParagraphStyle(
        "MedintelPointLabel",
        parent=body_style,
        fontName="Times-Bold",
    )

    story = []

    header_rows = [
        ["Kepada", f": {report.kepada}"],
        ["Dari", f": {report.dari}"],
    ]
    if report.tembusan:
        header_rows.append(["Tembusan", f": {report.tembusan}"])
    header_rows.append(
        ["Hal", f": {report.hal or '-'}"]
    )
    header_rows.append(["Nilai", f": {report.nilai or '-'}"])

    header_table = Table(
        [
            [
                Paragraph(f"<i>{label}</i>", header_label_style),
                Paragraph(f"<i>{value}</i>", header_label_style),
            ]
            for label, value in header_rows
        ],
        colWidths=[3 * cm, 13.5 * cm],
    )
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 0.6 * cm))

    sections = list(report.sections.all())

    def add_section(roman: str, heading: str, field_name: str):
        story.append(Paragraph(f"{roman}. {heading}", section_heading_style))
        for section in sections:
            label = section.disease.name if section.disease else "-"
            text = getattr(section, field_name) or "-"
            story.append(
                Paragraph(
                    f"<b>{section.letter}. {label}</b>"
                    if field_name == "indikasi_text"
                    else f"<b>{section.letter}.</b>",
                    point_label_style,
                )
            )
            story.append(Paragraph(text.replace("\n", "<br/>"), body_style))

    add_section("I", "INDIKASI", "indikasi_text")
    add_section("II", "ANALISIS", "analisis_text")
    add_section("III", "DAMPAK", "dampak_text")
    add_section("IV", "UPAYA", "upaya_text")
    add_section("V", "SARAN TINDAK", "saran_tindak_text")

    if report.signature_block:
        story.append(Spacer(1, 1 * cm))
        story.append(
            Paragraph(
                report.signature_block,
                ParagraphStyle(
                    "MedintelSignature",
                    parent=body_style,
                    alignment=2,  # right
                ),
            )
        )

    doc.build(story)
    buffer.seek(0)

    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="laporan_{report.report_date}.pdf"'
    )
    return response
