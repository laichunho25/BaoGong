"""Registration, login and email verification.

The views validate input and delegate every write to ``services.py``
(ARCHITECTURE section 3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.accounts import selectors as account_selectors
from apps.accounts import services
from apps.accounts.forms import EmailLoginForm, RegistrationForm
from apps.accounts.models import Role
from apps.core import turnstile
from apps.providers import selectors as provider_selectors

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

    from apps.accounts.models import User


def _client_ip(request: HttpRequest) -> str | None:
    return request.META.get("REMOTE_ADDR")


def register(request: HttpRequest) -> HttpResponse:
    """Create an account and start the email verification loop."""
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    if request.method == "POST":
        form = RegistrationForm(request.POST, remote_ip=_client_ip(request))
        if form.is_valid():
            user = services.register_user(
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
                role=form.cleaned_data["role"],
                phone=form.cleaned_data["phone"],
                request=request,
            )
            # Signed in immediately: the account can browse, and every action
            # that speaks to a real company checks is_email_verified anyway.
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, _("注册成功。我们已发送验证邮件，请查收。"))
            return redirect("accounts:verification_sent")
    else:
        form = RegistrationForm(remote_ip=_client_ip(request))

    return render(
        request,
        "accounts/register.html",
        {
            "form": form,
            "turnstile_enabled": turnstile.is_enabled(),
            "turnstile_site_key": settings.TURNSTILE_SITE_KEY,
        },
    )


class EmailLoginView(LoginView):
    """Django's login view with the email form and our template."""

    form_class = EmailLoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_default_redirect_url(self) -> str:
        return reverse("accounts:dashboard")


class SignOutView(LogoutView):
    """POST-only sign out (Django 5 no longer allows GET)."""

    next_page = "/"


def verification_sent(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/verification_sent.html")


def verify_email(request: HttpRequest, token: str) -> HttpResponse:
    """Consume a verification token.

    Reached from a mail client, so it has to answer a GET. The token is
    single-use, which is what keeps that from being a meaningful CSRF surface:
    a forged request can only spend a token the attacker already holds.
    """
    try:
        services.verify_email(token)
    except services.VerificationError:
        return render(request, "accounts/verify_failed.html", status=400)

    messages.success(request, _("邮箱验证成功。"))
    return redirect("accounts:dashboard" if request.user.is_authenticated else "accounts:login")


@login_required
def resend_verification(request: HttpRequest) -> HttpResponse:
    """Issue a fresh token. POST only: a GET could be triggered by an image."""
    if request.method != "POST":
        return redirect("accounts:verification_sent")

    user = request.user
    if user.is_email_verified:  # type: ignore[union-attr]
        messages.info(request, _("该邮箱已验证。"))
        return redirect("accounts:dashboard")

    issued = services.issue_email_verification(user)  # type: ignore[arg-type]
    services.send_verification_email(user, issued.token, request=request)  # type: ignore[arg-type]
    messages.success(request, _("验证邮件已重新发送。"))
    return redirect("accounts:verification_sent")


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """Where an account lands after signing in.

    Deliberately thin for now: it states verification status and points at the
    one thing each role can currently do.
    """
    context: dict[str, Any] = {
        "is_provider_member": request.user.role == Role.PROVIDER_MEMBER,  # type: ignore[union-attr]
        # An application takes days to decide, so the account page is where the
        # applicant comes back to check on it - and it is the only place the
        # claim's URL can be found again once the confirmation page is gone.
        "claims": provider_selectors.claims_for_user(str(request.user.pk)),
        # An approved claim is only worth having if the page it unlocked is
        # reachable from here; until this list existed, a company that claimed
        # its page had no way back to it except through public search.
        "managed_providers": provider_selectors.providers_for_member(str(request.user.pk)),
    }
    return render(request, "accounts/dashboard.html", context)


@login_required
def accept_invite(request: HttpRequest, token: str) -> HttpResponse:
    """Join a company's page, having been invited to it.

    Behind ``login_required`` on purpose. The point of the invitation flow is
    that a membership always has a person behind it who agreed to it, and the
    only way to know which person is reading the link is to make them sign in
    first. Someone without an account registers and comes back; the link lives
    long enough for that.

    Accepting is a write, so it needs POST. The GET only shows what is on
    offer, and says so plainly when the signed-in address is not the invited
    one - the commonest way this goes wrong is a colleague forwarding the mail.
    """
    invite = account_selectors.invite_for_token(token)
    if invite is None:
        return render(request, "accounts/invite_invalid.html", status=404)

    user = cast("User", request.user)
    mismatch = user.email.strip().lower() != invite.email
    if request.method == "POST" and not mismatch:
        try:
            membership = services.accept_invite(token=token, user=user)
        except services.MembershipError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request, _("已加入「%(name)s」。") % {"name": invite.provider.display_name}
            )
            return redirect("providers:manage", slug=membership.provider.slug)

    return render(request, "accounts/invite_accept.html", {"invite": invite, "mismatch": mismatch})
