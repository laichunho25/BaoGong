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
    path("dashboard/", views.dashboard, name="dashboard"),
]
