from django.urls import path

from . import views

app_name = "indicators"

urlpatterns = [
    path("review/", views.indicator_review, name="review"),
]
