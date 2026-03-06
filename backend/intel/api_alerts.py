from datetime import timedelta
from collections import defaultdict

from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import SignalLocation


def compute_risk_score(avg_threat, avg_severity, ratio, recent):
    """
    Risk score 0-100
    """
    ratio_scaled = min((ratio or 0) * 20, 100)
    recent_scaled = min((recent or 0) * 10, 100)

    score = (
        0.35 * (avg_threat or 0) +
        0.25 * (avg_severity or 0) +
        0.25 * ratio_scaled +
        0.15 * recent_scaled
    )

    return round(score, 2)


def classify_risk_level(score):
    if score >= 75:
        return "CRITICAL"
    elif score >= 50:
        return "HIGH"
    elif score >= 25:
        return "MEDIUM"
    return "LOW"


class OutbreakAlertAPI(APIView):
    """
    GET /api/alerts/outbreak/
    Alert berbasis disease + province
    """

    def get(self, request):
        now = timezone.now()

        # Default diperlebar supaya cocok untuk data media / RSS
        try:
            recent_days = int(request.query_params.get("recent_days", 7))
        except Exception:
            recent_days = 7

        try:
            baseline_days = int(request.query_params.get("baseline_days", 30))
        except Exception:
            baseline_days = 30

        try:
            min_score = int(request.query_params.get("min_score", 0))
        except Exception:
            min_score = 0

        recent_start = now - timedelta(days=recent_days)
        baseline_start = now - timedelta(days=baseline_days)

        qs = (
            SignalLocation.objects
            .select_related(
                "signal",
                "signal__source",
                "location",
                "location__parent",
                "location__parent__parent",
            )
            .filter(
                is_primary=True,
                geocode_status="ok",
                signal__threat_score__gte=min_score,
                signal__published_at__isnull=False,
                location__isnull=False,
            )
        )

        def walk_to_province(loc, max_hops=8):
            cur = loc
            for _ in range(max_hops):
                if not cur:
                    return None
                if getattr(cur, "level", None) == "province":
                    return cur
                cur = getattr(cur, "parent", None)
            return None

        recent_counts = defaultdict(int)
        baseline_counts = defaultdict(int)

        recent_score_sum = defaultdict(int)
        recent_severity_sum = defaultdict(int)
        recent_event_counter = defaultdict(lambda: defaultdict(int))

        total_recent_records = 0
        total_baseline_records = 0

        for sl in qs:
            signal = sl.signal
            province_obj = walk_to_province(sl.location)
            province = province_obj.name if province_obj else "Unknown"

            disease = signal.disease_tag or "Unknown"
            key = (disease, province)

            published_at = signal.published_at

            if published_at >= recent_start:
                total_recent_records += 1
                recent_counts[key] += 1
                recent_score_sum[key] += signal.threat_score or 0
                recent_severity_sum[key] += signal.severity_nlp or 0

                if signal.event_types:
                    for ev in str(signal.event_types).split("|"):
                        ev = ev.strip()
                        if ev:
                            recent_event_counter[key][ev] += 1

            elif baseline_start <= published_at < recent_start:
                total_baseline_records += 1
                baseline_counts[key] += 1

        alerts = []
        keys = set(list(recent_counts.keys()) + list(baseline_counts.keys()))

        for key in keys:
            disease, province = key
            r = recent_counts.get(key, 0)
            b = baseline_counts.get(key, 0)

            # longgarkan minimal recent count
            if r < 1:
                continue

            # jika baseline nol tapi recent ada, tetap munculkan sebagai sinyal baru
            if b == 0:
                if r >= 2:
                    ratio = float(r)
                    level = "WARNING" if r < 4 else "ALERT"
                else:
                    level = "WATCH"
                    ratio = float(r)
            else:
                ratio = r / b

                if ratio >= 3.0:
                    level = "ALERT"
                elif ratio >= 1.8:
                    level = "WARNING"
                elif ratio >= 1.2:
                    level = "WATCH"
                else:
                    continue

            avg_score = round(recent_score_sum[key] / r, 2) if r else 0
            avg_severity = round(recent_severity_sum[key] / r, 2) if r else 0

            top_events = sorted(
                recent_event_counter[key].items(),
                key=lambda x: x[1],
                reverse=True
            )
            top_event = top_events[0][0] if top_events else None

            risk_score = compute_risk_score(avg_score, avg_severity, ratio, r)
            risk_level = classify_risk_level(risk_score)

            alerts.append({
                "disease": disease,
                "province": province,
                "recent": r,
                "baseline": b,
                "increase_ratio": round(ratio, 2),
                "avg_score_recent": avg_score,
                "severity_avg": avg_severity,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "top_event_type": top_event,
                "level": level,
                "recent_days": recent_days,
                "baseline_days": baseline_days,
            })

        alerts.sort(
            key=lambda x: (
                {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(x["risk_level"], 0),
                {"ALERT": 3, "WARNING": 2, "WATCH": 1}.get(x["level"], 0),
                x["risk_score"],
                x["increase_ratio"],
                x["recent"],
            ),
            reverse=True
        )

        # return summary juga, supaya dashboard/debug tidak "diam kosong"
        return Response({
            "summary": {
                "recent_days": recent_days,
                "baseline_days": baseline_days,
                "min_score": min_score,
                "total_recent_records": total_recent_records,
                "total_baseline_records": total_baseline_records,
                "recent_group_count": len(recent_counts),
                "baseline_group_count": len(baseline_counts),
                "alert_count": len(alerts),
            },
            "results": alerts
        })