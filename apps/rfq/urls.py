"""Matching URLs."""

from django.urls import path

from apps.rfq import views

app_name = "rfq"

urlpatterns = [
    # Reserved first segments, before the id route.
    path("", views.rfq_wall, name="wall"),
    path("new/", views.rfq_create, name="create"),
    path("new/read/", views.rfq_intake, name="intake"),
    path("mine/", views.my_rfqs, name="my_rfqs"),
    path("<uuid:rfq_id>/", views.rfq_detail, name="detail"),
    path("<uuid:rfq_id>/publish/", views.rfq_publish, name="publish"),
    path("<uuid:rfq_id>/close/", views.rfq_close, name="close"),
    # The company is in the path because a person may act for more than one,
    # and an offer sent under the wrong name is not a slip anyone can undo.
    path("<uuid:rfq_id>/quote/<slug:slug>/", views.quote_create, name="quote"),
    path("quotes/<uuid:quote_id>/withdraw/", views.quote_withdraw, name="quote_withdraw"),
    path("quotes/<uuid:quote_id>/shortlist/", views.quote_shortlist, name="quote_shortlist"),
    path("quotes/<uuid:quote_id>/accept/", views.quote_accept, name="quote_accept"),
]
