"""Public directory URLs."""

from django.urls import path

from apps.providers import views

app_name = "providers"

urlpatterns = [
    path("", views.provider_list, name="list"),
    # Before the slug route: "claims" and "logos" are reserved first segments,
    # and these paths carry a row id rather than a provider slug.
    path("claims/<uuid:claim_id>/", views.claim_detail, name="claim_detail"),
    path("claims/<uuid:claim_id>/verify/", views.claim_verify_website, name="claim_verify"),
    path("claims/<uuid:claim_id>/withdraw/", views.claim_withdraw, name="claim_withdraw"),
    path(
        "claims/evidence/<uuid:evidence_id>/",
        views.claim_evidence_download,
        name="claim_evidence",
    ),
    path("logos/<uuid:logo_id>/", views.provider_logo_preview, name="logo_preview"),
    path("logos/<uuid:logo_id>/withdraw/", views.provider_logo_withdraw, name="logo_withdraw"),
    path("<slug:slug>/claim/", views.claim_start, name="claim_start"),
    path("<slug:slug>/manage/", views.provider_manage, name="manage"),
    path("<slug:slug>/manage/logo/", views.provider_logo_upload, name="logo_upload"),
    path("<slug:slug>/manage/team/", views.provider_team, name="team"),
    path(
        "<slug:slug>/manage/team/<uuid:member_id>/role/",
        views.provider_member_role,
        name="member_role",
    ),
    path(
        "<slug:slug>/manage/team/<uuid:member_id>/remove/",
        views.provider_member_remove,
        name="member_remove",
    ),
    path(
        "<slug:slug>/manage/team/invites/<uuid:invite_id>/revoke/",
        views.provider_invite_revoke,
        name="invite_revoke",
    ),
    path("<slug:slug>/", views.provider_detail, name="detail"),
]
