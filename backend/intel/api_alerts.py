from datetime import timedelta
from collections import defaultdict
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response

from .models import Signal


class OutbreakAlertAPI(APIView):

    def get(self, request):

        now = timezone.now()

        recent_days = 2
        baseline_days = 7

        recent_start = now - timedelta(days=recent_days)
        baseline_start = now - timedelta(days=baseline_days)

        recent_qs = Signal.objects.filter(
            published_at__gte=recent_start
        )

        baseline_qs = Signal.objects.filter(
            published_at__gte=baseline_start,
            published_at__lt=recent_start
        )

        recent = defaultdict(int)
        baseline = defaultdict(int)

        for s in recent_qs.only("disease_tag"):
            recent[s.disease_tag] += 1

        for s in baseline_qs.only("disease_tag"):
            baseline[s.disease_tag] += 1

        alerts = []

        diseases = set(list(recent.keys()) + list(baseline.keys()))

        for d in diseases:

            r = recent.get(d, 0)
            b = baseline.get(d, 0)

            if b == 0:
                continue

            ratio = r / b

            level = None

            if ratio >= 4:
                level = "ALERT"
            elif ratio >= 2.5:
                level = "WARNING"
            elif ratio >= 1.5:
                level = "WATCH"

            if level:

                alerts.append({
                    "disease": d,
                    "recent": r,
                    "baseline": b,
                    "increase_ratio": round(ratio,2),
                    "level": level
                })

        alerts.sort(key=lambda x: x["increase_ratio"], reverse=True)

        return Response(alerts)