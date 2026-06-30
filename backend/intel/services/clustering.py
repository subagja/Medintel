# backend/intel/services/clustering.py

from datetime import timedelta

from django.db.models import Avg, Max, Count, Q
from django.utils.text import slugify

from intel.models import Signal, SignalCluster
from intel.services.disease_master import match_disease_master


def _get_risk_score(signal):
    return (
        getattr(signal, "threat_score", None)
        or getattr(signal, "score", None)
        or 0
    )


def _get_primary_signal_location(signal):
    try:
        return (
            signal.locations
            .select_related("location", "location__parent")
            .filter(location__isnull=False)
            .first()
        )
    except Exception:
        return None


def _get_location_identity(signal):
    sl = _get_primary_signal_location(signal)

    if sl and sl.location:
        loc = sl.location
        name = (
            getattr(loc, "normalized_name", None)
            or getattr(loc, "display_name", None)
            or getattr(loc, "name", None)
            or "unknown_location"
        )
        level = getattr(loc, "level", "") or ""
        return name, level

    raw_location = (
        getattr(signal, "admin_kabkota", None)
        or getattr(signal, "admin_province", None)
        or getattr(signal, "raw_location_text", None)
        or "unknown_location"
    )

    return raw_location, ""


def _get_cluster_date(signal):
    if getattr(signal, "published_at", None):
        return signal.published_at.date()
    if getattr(signal, "created_at", None):
        return signal.created_at.date()
    return None


def build_cluster_key(signal):
    disease = getattr(signal, "disease_tag", None) or "unknown_disease"
    location_name, _level = _get_location_identity(signal)
    date_value = _get_cluster_date(signal)

    # Awal dibuat harian agar deteksi dini tetap sensitif.
    date_part = date_value.isoformat() if date_value else "unknown_date"

    return "|".join(
        [
            slugify(str(disease)) or "unknown-disease",
            slugify(str(location_name)) or "unknown-location",
            date_part,
        ]
    )


def summarize_cluster(cluster):
    disease = cluster.disease_tag or "penyakit belum jelas"
    location = cluster.location_name or "lokasi belum jelas"

    summary = (
        f"Terdeteksi {cluster.signal_count} signal terkait {disease} "
        f"di {location} pada periode {cluster.date_start} s.d. {cluster.date_end}. "
        f"Rata-rata skor risiko {cluster.avg_score:.1f}, skor maksimum "
        f"{cluster.max_score:.1f}, dan rata-rata confidence "
        f"{cluster.avg_confidence:.1f}."
    )

    reasons = [
        f"Jumlah signal: {cluster.signal_count}",
        f"Raw: {cluster.raw_count}",
        f"Validated: {cluster.validated_count}",
        f"Verified: {cluster.verified_count}",
        f"Noise: {cluster.noise_count}",
        f"Avg score: {cluster.avg_score:.1f}",
        f"Max score: {cluster.max_score:.1f}",
        f"Avg confidence: {cluster.avg_confidence:.1f}",
    ]

    if cluster.priority in ["urgent", "high"]:
        recommendation = (
            "Direkomendasikan untuk diprioritaskan dalam review analis "
            "dan dipertimbangkan untuk batch validation/approval apabila "
            "signal di dalamnya telah memenuhi kriteria validasi."
        )
    elif cluster.priority == "medium":
        recommendation = (
            "Direkomendasikan untuk review manual, terutama memastikan "
            "kejelasan lokasi, penyakit, dan duplikasi."
        )
    else:
        recommendation = (
            "Prioritas rendah. Review dapat dilakukan setelah cluster "
            "urgent/high selesai."
        )

    return summary, recommendation, "\n".join(reasons)


def calculate_cluster_priority(signal_count, max_score, avg_score, avg_confidence):
    if signal_count >= 3 and max_score >= 75 and avg_confidence >= 70:
        return "urgent"
    if signal_count >= 2 and max_score >= 60 and avg_confidence >= 55:
        return "high"
    if avg_score >= 40 or avg_confidence >= 50:
        return "medium"
    return "low"


def rebuild_cluster_aggregate(cluster):
    qs = cluster.signals.all()

    signal_count = qs.count()
    raw_count = qs.filter(status="raw").count()
    validated_count = qs.filter(status="validated").count()
    verified_count = qs.filter(status__in=["validated", "approved"]).count()
    noise_count = qs.filter(status="noise").count()

    non_noise_qs = qs.exclude(status="noise")

    agg = non_noise_qs.aggregate(
        avg_score=Avg("threat_score"),
        max_score=Max("threat_score"),
        avg_confidence=Avg("confidence_score"),
    )

    avg_score = agg["avg_score"] or 0
    max_score = agg["max_score"] or 0
    avg_confidence = agg["avg_confidence"] or 0

    dates = [
        _get_cluster_date(signal)
        for signal in qs
        if _get_cluster_date(signal) is not None
    ]

    cluster.signal_count = signal_count
    cluster.raw_count = raw_count
    cluster.validated_count = validated_count
    cluster.verified_count = verified_count
    cluster.noise_count = noise_count

    cluster.avg_score = round(avg_score, 2)
    cluster.max_score = round(max_score, 2)
    cluster.avg_confidence = round(avg_confidence, 2)

    cluster.date_start = min(dates) if dates else None
    cluster.date_end = max(dates) if dates else None

    cluster.priority = calculate_cluster_priority(
        signal_count=signal_count,
        max_score=max_score,
        avg_score=avg_score,
        avg_confidence=avg_confidence,
    )

    summary, recommendation, reason = summarize_cluster(cluster)

    cluster.summary = summary
    cluster.recommendation = recommendation
    cluster.reason = reason

    if not cluster.disease_master_id:
        cluster.disease_master = match_disease_master(disease_tag=cluster.disease_tag)

    cluster.save()

    return cluster


def assign_signal_to_cluster(signal):
    cluster_key = build_cluster_key(signal)
    disease = getattr(signal, "disease_tag", None) or ""
    location_name, location_level = _get_location_identity(signal)
    date_value = _get_cluster_date(signal)
    disease_master = (
        getattr(signal, "disease_master", None)
        or match_disease_master(
            disease_tag=disease,
            title=getattr(signal, "title", "") or "",
            content=getattr(signal, "content", "") or "",
        )
    )

    cluster, _created = SignalCluster.objects.get_or_create(
        cluster_key=cluster_key,
        defaults={
            "disease_tag": disease,
            "disease_master": disease_master,
            "location_name": location_name,
            "location_level": location_level,
            "date_start": date_value,
            "date_end": date_value,
        },
    )

    if signal.cluster_id != cluster.id:
        signal.cluster = cluster
        signal.save(update_fields=["cluster"])

    return cluster
