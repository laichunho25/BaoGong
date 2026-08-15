"""Review URLs."""

from django.urls import path

from apps.reviews import views

app_name = "reviews"

urlpatterns = [
    # Before the slug route: "mine" is a reserved first segment.
    path("mine/", views.my_reviews, name="my_reviews"),
    path("<uuid:review_id>/reply/", views.review_reply, name="reply"),
    path("<uuid:review_id>/verify/", views.nnc1_upload, name="nnc1_upload"),
    path("<uuid:review_id>/dispute/", views.dispute_create, name="dispute"),
    # Not a storage URL: the view re-checks who is asking and whether the file
    # has been scanned. See providers/urls.py for the same shape.
    path("verification/<uuid:verification_id>/file/", views.nnc1_download, name="nnc1_document"),
    # The form belongs to one company, so its URL carries the provider slug.
    path("<slug:slug>/new/", views.review_create, name="create"),
]
