"""Review URLs."""

from django.urls import path

from apps.reviews import views

app_name = "reviews"

urlpatterns = [
    # Before the slug route: "mine" is a reserved first segment.
    path("mine/", views.my_reviews, name="my_reviews"),
    path("<uuid:review_id>/reply/", views.review_reply, name="reply"),
    # The form belongs to one company, so its URL carries the provider slug.
    path("<slug:slug>/new/", views.review_create, name="create"),
]
