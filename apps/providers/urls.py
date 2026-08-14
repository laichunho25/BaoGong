"""Public directory URLs."""

from django.urls import path

from apps.providers import views

app_name = "providers"

urlpatterns = [
    path("", views.provider_list, name="list"),
    # Before the slug route: "claims" is a reserved first segment, and these
    # paths carry a claim id rather than a provider slug.
    path("claims/<uuid:claim_id>/", views.claim_detail, name="claim_detail"),
    path("claims/<uuid:claim_id>/verify/", views.claim_verify_website, name="claim_verify"),
    path("claims/<uuid:claim_id>/withdraw/", views.claim_withdraw, name="claim_withdraw"),
    path(
        "claims/evidence/<uuid:evidence_id>/",
        views.claim_evidence_download,
        name="claim_evidence",
    ),
    path("<slug:slug>/claim/", views.claim_start, name="claim_start"),
    path("<slug:slug>/", views.provider_detail, name="detail"),
]
