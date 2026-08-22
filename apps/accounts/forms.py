"""Forms for the account flows.

Validation only. The forms never save - ``views.py`` hands the cleaned data to
``services.py`` (ARCHITECTURE section 3).
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.password_validation import (
    password_validators_help_texts,
    validate_password,
)
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import MemberRole, Role
from apps.core import turnstile
from apps.core.form_styles import INPUT_CLASSES
from apps.core.validators import PHONE_INPUT_ATTRS, normalise_phone, validate_phone


class TurnstileMixin(forms.Form):
    """Adds the Cloudflare challenge field, when a key is configured.

    The field is declared unconditionally so the template can render it, and
    only enforced when Turnstile is switched on - a local developer without a
    Cloudflare account still has to be able to register.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.remote_ip: str | None = kwargs.pop("remote_ip", None)
        super().__init__(*args, **kwargs)

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        if turnstile.is_enabled():
            token = self.data.get(turnstile.FIELD_NAME, "")
            if not turnstile.verify(token, remote_ip=self.remote_ip):
                raise ValidationError(_("人机验证未通过，请重试。"))
        return cleaned


class RegistrationForm(TurnstileMixin, forms.Form):
    email = forms.EmailField(
        label=_("邮箱"),
        widget=forms.EmailInput(attrs={"class": INPUT_CLASSES, "autocomplete": "email"}),
    )
    password = forms.CharField(
        label=_("密码"),
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASSES, "autocomplete": "new-password"}),
    )
    phone = forms.CharField(
        label=_("手机号（选填）"),
        required=False,
        max_length=32,
        validators=[validate_phone],
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASSES, "autocomplete": "tel", **PHONE_INPUT_ATTRS}
        ),
    )
    role = forms.ChoiceField(
        label=_("我是"),
        choices=[
            (Role.BUYER, _("客户 - 我要找秘书公司")),
            (Role.PROVIDER_MEMBER, _("秘书公司 - 我要认领本公司页面")),
        ],
        initial=Role.BUYER,
        widget=forms.RadioSelect,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Filled in here rather than on the field so the rules are rendered in
        # the language of the request, and so that a change to
        # AUTH_PASSWORD_VALIDATORS shows up on the form without an edit here.
        # The rules are shown before the box is typed in, not after it is
        # rejected: a policy a visitor only meets by failing it is a policy
        # they meet several times.
        self.fields["password"].help_text = " ".join(password_validators_help_texts())

    def clean_email(self) -> str:
        email = str(self.cleaned_data["email"]).strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise ValidationError(_("该邮箱已注册，请直接登录。"))
        return email

    def clean_password(self) -> str:
        password = str(self.cleaned_data["password"])
        validate_password(password)
        return password

    def clean_phone(self) -> str:
        """Store the digits, not the punctuation somebody typed around them."""
        return normalise_phone(str(self.cleaned_data.get("phone") or ""))


class EmailLoginForm(AuthenticationForm):
    """Django's login form, relabelled for an email identifier.

    It also refuses an account whose address has never been confirmed. The
    order is deliberate - verify, then sign in - because everything an account
    can do here reaches a real, named, licensed company: an unconfirmed
    mailbox can otherwise post a review, send a requirement carrying somebody
    else's phone number, or apply for control of a page, and the only address
    we have to answer for it is one nobody has proved they can read.
    """

    username = forms.EmailField(
        label=_("邮箱"),
        widget=forms.EmailInput(attrs={"class": INPUT_CLASSES, "autocomplete": "email"}),
    )
    password = forms.CharField(
        label=_("密码"),
        widget=forms.PasswordInput(
            attrs={"class": INPUT_CLASSES, "autocomplete": "current-password"}
        ),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "unverified": _("请先完成邮箱验证再登录。我们已向该邮箱发送过验证链接。"),
    }

    def clean_username(self) -> str:
        return str(self.cleaned_data["username"]).strip().lower()

    def confirm_login_allowed(self, user: Any) -> None:
        super().confirm_login_allowed(user)
        if not user.is_email_verified:
            # Carried on the exception so the view can offer the resend form
            # for this address without asking the visitor to type it again.
            self.unverified_email = user.email
            raise ValidationError(self.error_messages["unverified"], code="unverified")


class ResendVerificationForm(forms.Form):
    """Ask for the verification mail again, without being signed in.

    Signing in is exactly what the person cannot do yet, so this form is
    reachable while anonymous. It never says whether the address is registered
    - the view answers the same way either way (see ``views.resend_verification``).
    """

    email = forms.EmailField(
        label=_("邮箱"),
        widget=forms.EmailInput(attrs={"class": INPUT_CLASSES, "autocomplete": "email"}),
    )

    def clean_email(self) -> str:
        return str(self.cleaned_data["email"]).strip().lower()


class ForgotPasswordForm(PasswordResetForm):
    """Django's reset request, restyled and normalised to a lower-case address.

    No "no such account" message here either, for the usual reason: a form
    that distinguishes the two is a free list of which addresses hold an
    account on a platform where the account belongs to a named company.
    """

    email = forms.EmailField(
        label=_("邮箱"),
        max_length=254,
        widget=forms.EmailInput(attrs={"class": INPUT_CLASSES, "autocomplete": "email"}),
    )

    def clean_email(self) -> str:
        return str(self.cleaned_data["email"]).strip().lower()


# django-stubs types SetPasswordForm as generic over the user model; the
# runtime class is not subscriptable, so the parameter cannot be written here.
class ChooseNewPasswordForm(SetPasswordForm):  # type: ignore[type-arg]
    """The second half of the reset, restyled and with the rules on show."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].label = _("新密码")
        self.fields["new_password1"].help_text = " ".join(password_validators_help_texts())
        self.fields["new_password2"].label = _("再输入一次")
        self.fields["new_password2"].help_text = ""
        for name in ("new_password1", "new_password2"):
            self.fields[name].widget.attrs.update(
                {"class": INPUT_CLASSES, "autocomplete": "new-password"}
            )


class MemberInviteForm(forms.Form):
    """Invite a colleague onto a company's page.

    Only the address and the role. Whether the invitation may be sent at all -
    who is asking, whether the page is still on the register, whether that
    mailbox is already a member - is decided in ``services.invite_member``,
    which is also reachable from the shell and the admin.
    """

    email = forms.EmailField(
        label=_("同事邮箱"),
        help_text=_("我们会发送邀请链接。对方需用这个邮箱登录并接受后才会成为成员。"),
        widget=forms.EmailInput(attrs={"class": INPUT_CLASSES, "autocomplete": "off"}),
    )
    member_role = forms.ChoiceField(
        label=_("角色"),
        choices=MemberRole.choices,
        initial=MemberRole.STAFF,
        help_text=_("员工可以编辑页面、回复评价；拥有者还可以管理成员。"),
        widget=forms.Select(attrs={"class": INPUT_CLASSES}),
    )
