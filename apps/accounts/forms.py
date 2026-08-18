"""Forms for the account flows.

Validation only. The forms never save - ``views.py`` hands the cleaned data to
``services.py`` (ARCHITECTURE section 3).
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import Role
from apps.core import turnstile
from apps.core.form_styles import INPUT_CLASSES


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
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES, "autocomplete": "tel"}),
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

    def clean_email(self) -> str:
        email = str(self.cleaned_data["email"]).strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise ValidationError(_("该邮箱已注册，请直接登录。"))
        return email

    def clean_password(self) -> str:
        password = str(self.cleaned_data["password"])
        validate_password(password)
        return password


class EmailLoginForm(AuthenticationForm):
    """Django's login form, relabelled for an email identifier."""

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

    def clean_username(self) -> str:
        return str(self.cleaned_data["username"]).strip().lower()
