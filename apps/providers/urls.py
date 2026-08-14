"""Public directory URLs."""

from django.urls import path

from apps.providers import views

app_name = "providers"

urlpatterns = [
    path("", views.provider_list, name="list"),
    path("<slug:slug>/", views.provider_detail, name="detail"),
]
