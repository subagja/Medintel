from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from .services.executive_dashboard import (
    build_executive_dashboard_dataset,
)


def executive_dashboard(request: HttpRequest) -> HttpResponse:
    context = {
        "page_title": "Dashboard Eksekutif",
        "active_menu": "executive_dashboard",
        "dashboard": build_executive_dashboard_dataset(),
    }
    return render(
        request,
        "assessments/executive_dashboard.html",
        context,
    )
