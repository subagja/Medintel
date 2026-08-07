from apps.sources.models import Source


def pending_source_verification(request):
    """Jumlah sumber yang siap dites (punya seed + pola allow aktif)
    tapi belum diverifikasi, dipakai untuk badge di sidebar.

    Query ini sengaja dibuat ringan (hanya count, tanpa prefetch) karena
    dijalankan di SETIAP request lewat context processor.
    """
    count = (
        Source.objects.filter(
            is_verified=False,
            seed_urls__is_active=True,
            url_patterns__is_active=True,
            url_patterns__pattern_type="allow",
        )
        .distinct()
        .count()
    )

    return {"pending_source_verification_count": count}
