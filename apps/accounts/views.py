"""Registration, login and email verification.

The views validate input and delegate every write to ``services.py``
(ARCHITECTURE section 3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts import selectors as account_selectors
from apps.accounts import services
from apps.accounts.forms import (
    ChooseNewPasswordForm,
    EmailLoginForm,
    ForgotPasswordForm,
    RegistrationForm,
    ResendVerificationForm,
)
from apps.accounts.models import Role
from apps.core import throttling, turnstile
from apps.providers import selectors as provider_selectors

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

    from apps.accounts.models import User

#: Where the address a visitor just registered (or failed to sign in as) is
#: kept, so the "check your mail" page can name it and offer to send again
#: without asking them to type it a third time. The session, not the URL: an
#: address in a query string ends up in proxy logs and browser history.
PENDING_EMAIL_KEY = "pending_verification_email"

#: Sign-in attempts allowed per address, per caller, per window. Six is
#: several typos and no dictionary.
LOGIN_ATTEMPTS = 6
LOGIN_WINDOW_SECONDS = 15 * 60

#: Mails a caller may cause to be sent. These are the two forms on the site
#: that make the server post something to an address the caller names, so they
#: are also the two that can be pointed at somebody else's inbox.
MAIL_REQUESTS = 4
MAIL_WINDOW_SECONDS = 60 * 60


def _client_ip(request: HttpRequest) -> str | None:
    return request.META.get("REMOTE_ADDR")


class AnonymousOnlyMixin:
    """Send a signed-in visitor to their dashboard instead of showing the page.

    Registration, sign-in and the whole password-reset flow are pages for
    somebody who is not signed in. Left reachable, they are a way for a shared
    or unattended browser to open a second account, or to walk a reset through
    on a session its owner never left - the value of "you are already signed
    in" is that it is stated rather than assumed.
    """

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if request.user.is_authenticated:
            return redirect("accounts:dashboard")
        # The mixin sits in front of a Django view class, which mypy cannot
        # see from here; the cast is to the response every such view returns.
        return cast("HttpResponse", super().dispatch(request, *args, **kwargs))  # type: ignore[misc]


def register(request: HttpRequest) -> HttpResponse:
    """Create an account and start the email verification loop.

    Registration no longer signs the account in. Verify first, then sign in
    with the password: until the link in the mail has been clicked, all we
    have is somebody's claim about an address, and a session handed out on
    that claim is a session that can post under it.
    """
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    if request.method == "POST":
        form = RegistrationForm(request.POST, remote_ip=_client_ip(request))
        if form.is_valid():
            services.register_user(
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
                role=form.cleaned_data["role"],
                phone=form.cleaned_data["phone"],
                request=request,
            )
            request.session[PENDING_EMAIL_KEY] = form.cleaned_data["email"]
            messages.success(request, _("注册成功。我们已发送验证邮件，请查收后登录。"))
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
    """Django's login view with the email form, rate limited, verified only.

    ``redirect_authenticated_user`` is what stops a signed-in browser from
    opening this page at all; the guessing limit is what stops it being worth
    opening repeatedly.
    """

    form_class = EmailLoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_default_redirect_url(self) -> str:
        return reverse("accounts:dashboard")

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Count the attempt before checking the password.

        Keyed on caller **and** address rather than on either alone: on the
        address alone, anyone could lock a company out of its own page by
        typing its address wrongly a few times; on the caller alone, a shared
        office NAT would lock out a floor.
        """
        self._throttle_key = throttling.client_key(
            request, scope="login", subject=request.POST.get("username", "")
        )
        if throttling.too_many(
            self._throttle_key, limit=LOGIN_ATTEMPTS, window_seconds=LOGIN_WINDOW_SECONDS
        ):
            messages.error(request, _("尝试次数过多，请稍后再试。"))
            return self.render_to_response(self.get_context_data(form=self.get_form()), status=429)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form: Any) -> HttpResponse:
        throttling.forget(getattr(self, "_throttle_key", ""))
        self.request.session.pop(PENDING_EMAIL_KEY, None)
        return super().form_valid(form)

    def form_invalid(self, form: Any) -> HttpResponse:
        """Send an unverified account to the page that can help it.

        The refusal message on its own leaves the person nowhere: the mail is
        48 hours old, probably in a spam folder, and the button that sends a
        new one used to be behind the sign-in they cannot complete.
        """
        pending = getattr(form, "unverified_email", "")
        if pending:
            self.request.session[PENDING_EMAIL_KEY] = pending
            messages.error(self.request, str(form.errors.get("__all__", [""])[0]))
            return redirect("accounts:verification_sent")
        return super().form_invalid(form)


class SignOutView(LogoutView):
    """POST-only sign out (Django 5 no longer allows GET)."""

    next_page = "/"


def verification_sent(request: HttpRequest) -> HttpResponse:
    """ "We have sent you a link" - and the way to ask for another one.

    Anonymous by design: after registration, and after a refused sign-in, the
    person standing here has no session. The address comes from the session
    where we know it, and from the form where we do not.
    """
    pending = request.session.get(PENDING_EMAIL_KEY, "")
    if not pending and request.user.is_authenticated:
        pending = request.user.email
    return render(
        request,
        "accounts/verification_sent.html",
        {"pending_email": pending, "form": ResendVerificationForm(initial={"email": pending})},
    )


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


@require_POST
def resend_verification(request: HttpRequest) -> HttpResponse:
    """Issue a fresh verification token for an address.

    POST only: a GET could be triggered by an ``<img>`` on any page on the
    internet, and this one sends mail.

    No login required - the account that needs this is precisely the one that
    cannot sign in. Three consequences are handled here: the address is taken
    from the form, the answer is the same whether or not it is registered, and
    the caller may only cause a few of these an hour.
    """
    if request.user.is_authenticated and request.user.is_email_verified:
        messages.info(request, _("该邮箱已验证。"))
        return redirect("accounts:dashboard")

    form = ResendVerificationForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "accounts/verification_sent.html",
            {"form": form, "pending_email": request.session.get(PENDING_EMAIL_KEY, "")},
            status=400,
        )

    email = form.cleaned_data["email"]
    key = throttling.client_key(request, scope="verify-resend", subject=email)
    if throttling.too_many(key, limit=MAIL_REQUESTS, window_seconds=MAIL_WINDOW_SECONDS):
        messages.error(request, _("请求过于频繁，请稍后再试。"))
        return redirect("accounts:verification_sent")

    services.resend_verification_email(email, request=request)
    request.session[PENDING_EMAIL_KEY] = email
    # Deliberately unconditional: "we have sent it" for an address that holds
    # no account is the only answer that does not turn this form into a way of
    # testing which addresses are registered here.
    messages.success(request, _("如果该邮箱已注册且尚未验证，验证邮件已重新发送。"))
    return redirect("accounts:verification_sent")


class ForgotPasswordView(AnonymousOnlyMixin, PasswordResetView):
    """Ask for a reset link.

    Rate limited on the caller, because each submission makes the server post
    a mail to an address the caller chose. The answer never says whether the
    address is registered - ``ForgotPasswordForm`` explains why.
    """

    form_class = ForgotPasswordForm
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/email/password_reset.txt"
    subject_template_name = "accounts/email/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def form_valid(self, form: Any) -> HttpResponse:
        key = throttling.client_key(
            request=self.request, scope="password-reset", subject=form.cleaned_data["email"]
        )
        if throttling.too_many(key, limit=MAIL_REQUESTS, window_seconds=MAIL_WINDOW_SECONDS):
            # Same destination as success. A throttle that announces itself
            # tells a script which addresses were worth asking about.
            return redirect(self.success_url)
        return super().form_valid(form)


class ForgotPasswordDoneView(AnonymousOnlyMixin, PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class ChooseNewPasswordView(AnonymousOnlyMixin, PasswordResetConfirmView):
    """Set the new password, having arrived from the link in the mail.

    Django moves the token out of the URL into the session before rendering
    the form, so the token does not sit in the browser history or leak through
    a Referer header on the next click.
    """

    form_class = ChooseNewPasswordForm
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form: Any) -> HttpResponse:
        response = super().form_valid(form)
        # The link went to the address and came back used, which is the proof
        # the verification mail asks for. See services.mark_email_verified.
        if self.user is not None:
            services.mark_email_verified(self.user)
        return response


class PasswordResetFinishedView(AnonymousOnlyMixin, PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


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
