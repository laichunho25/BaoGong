"""Account URLs."""

from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.EmailLoginView.as_view(), name="login"),
    path("logout/", views.SignOutView.as_view(), name="logout"),
    path("verify/sent/", views.verification_sent, name="verification_sent"),
    path("verify/resend/", views.resend_verification, name="resend_verification"),
    path("verify/<str:token>/", views.verify_email, name="verify_email"),
    # Django's own names are not reused ("password_reset" etc. are taken by
    # the admin's URLs when the console is mounted); these are ours, under a
    # path a person could read out over the phone.
    path("password/forgot/", views.ForgotPasswordView.as_view(), name="password_reset"),
    path("password/sent/", views.ForgotPasswordDoneView.as_view(), name="password_reset_done"),
    path(
        "password/reset/<uidb64>/<token>/",
        views.ChooseNewPasswordView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password/done/",
        views.PasswordResetFinishedView.as_view(),
        name="password_reset_complete",
    ),
    path("invites/<str:token>/", views.accept_invite, name="accept_invite"),
    path("dashboard/", views.dashboard, name="dashboard"),
]
