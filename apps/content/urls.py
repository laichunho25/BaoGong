"""Education library URLs."""

from django.urls import path

from apps.content import views

app_name = "content"

urlpatterns = [
    path("", views.article_index, name="list"),
    path("ask/", views.ask, name="ask"),
    path("<slug:slug>/", views.article_detail, name="detail"),
]
