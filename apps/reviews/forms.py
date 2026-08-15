"""The review form.

Validation only; ``views.py`` hands the cleaned data to ``services.py``
(ARCHITECTURE section 3).

The sub-scores are radio groups rather than a free number field: the product
defines nine legal values (1 to 5, step 0.5) and a text input would invite the
tenth. ``bank_support`` additionally allows "did not use it", which is the only
way a reviewer can decline to rate a service they never bought.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core import turnstile
from apps.providers.models import ServiceCategory
from apps.reviews.models import SCORE_FIELDS

INPUT_CLASSES = (
    "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm "
    "focus:border-slate-500 focus:outline-none"
)

#: 1 - 5 in halves (RATING_SYSTEM section 3), highest first so the radio row
#: reads the way a star rating does.
SCORE_CHOICES = [
    (str(value), str(value))
    for value in ((Decimal(step) / 2).quantize(Decimal("0.1")) for step in range(10, 1, -1))
]

NOT_APPLICABLE = ""

MIN_BODY_LENGTH = 30

SCORE_LABELS = {
    "price_transparency": _("报价透明度"),
    "responsiveness": _("响应速度"),
    "bank_support": _("开户协助"),
    "professionalism": _("专业程度"),
    "after_sales": _("售后服务"),
}


class ReviewForm(forms.Form):
    """One buyer's review of one company."""

    body = forms.CharField(
        label=_("你的经历"),
        min_length=MIN_BODY_LENGTH,
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 6}),
        help_text=_(
            "请写具体经历（服务内容、时间、沟通过程），至少 %(n)s 字。"
            "避免人身攻击、未经证实的指控，以及他人的姓名、电话等个人资料。"
        )
        % {"n": MIN_BODY_LENGTH},
    )
    service_used = forms.MultipleChoiceField(
        label=_("使用过的服务"),
        choices=ServiceCategory.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    engagement_year = forms.IntegerField(
        label=_("合作年份（选填）"),
        required=False,
        min_value=1990,
        max_value=2100,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASSES, "placeholder": "2025"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.remote_ip: str | None = kwargs.pop("remote_ip", None)
        super().__init__(*args, **kwargs)
        for field in SCORE_FIELDS:
            optional = field == "bank_support"
            choices: list[tuple[str, Any]] = list(SCORE_CHOICES)
            if optional:
                choices = [(NOT_APPLICABLE, _("未使用该服务")), *SCORE_CHOICES]
            self.fields[field] = forms.ChoiceField(
                label=SCORE_LABELS[field],
                choices=choices,
                required=not optional,
                widget=forms.RadioSelect,
            )

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        # RATING_SYSTEM section 6 pairs the verified account with a challenge;
        # the account check is the view's, the challenge is here.
        if turnstile.is_enabled():
            token = self.data.get(turnstile.FIELD_NAME, "")
            if not turnstile.verify(token, remote_ip=self.remote_ip):
                raise forms.ValidationError(_("人机验证未通过，请重试。"))
        return cleaned

    def scores(self) -> dict[str, Decimal | None]:
        """The five sub-scores, with "did not use it" as None rather than 0."""
        values: dict[str, Decimal | None] = {}
        for field in SCORE_FIELDS:
            raw = self.cleaned_data.get(field) or ""
            values[field] = Decimal(raw) if raw else None
        return values


class ReplyForm(forms.Form):
    """The company's right of reply (COMPLIANCE section 3)."""

    body = forms.CharField(
        label=_("公开回复"),
        max_length=2000,
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 4}),
        help_text=_("回复将公开显示在该评价下方，且每条评价只能回复一次。"),
    )
