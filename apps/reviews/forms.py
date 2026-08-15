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
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.core import turnstile
from apps.core.uploads import MAX_UPLOAD_BYTES, InspectedUpload, inspect_upload
from apps.providers.models import ServiceCategory
from apps.reviews.models import SCORE_FIELDS, DisputeGround

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

#: A dispute is read by a person on a five-working-day clock, so it has to say
#: something. Longer than a review's floor: "this is false" is not a case.
MIN_DISPUTE_REASON_LENGTH = 50

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


class DisputeForm(forms.Form):
    """A company's appeal against a review (COMPLIANCE section 3).

    The help text states plainly that filing this does not take the review
    down, because a form that looks like a takedown button will be used as one.
    ``ground`` is a closed list so the queue can be sorted by how checkable the
    complaint is - "was never our client" can be tested against the register,
    "we disagree" cannot.
    """

    ground = forms.ChoiceField(
        label=_("申诉理由类别"),
        choices=DisputeGround.choices,
        widget=forms.RadioSelect,
    )
    reason = forms.CharField(
        label=_("详细说明"),
        min_length=MIN_DISPUTE_REASON_LENGTH,
        max_length=4000,
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 6}),
        help_text=_(
            "请说明具体情况，并提供可核对的线索（如业务编号、往来日期）。"
            "提交申诉不会立即隐藏该评价；审核人员会在 %(days)s 个工作日内处理并回复理由。"
        )
        % {"days": settings.DISPUTE_SLA_BUSINESS_DAYS},
    )
    engagement_ref = forms.CharField(
        label=_("业务编号或往来凭证编号（选填）"),
        max_length=120,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )

    def evidence(self) -> dict[str, str]:
        """The structured half of the appeal, kept apart from the free text.

        No file field: an uploaded document would need the same scanning,
        private storage and retention clock as an NNC1, which is a feature and
        not a widget (see ROADMAP).
        """
        ref = self.cleaned_data.get("engagement_ref", "").strip()
        return {"engagement_ref": ref} if ref else {}


class Nnc1UploadForm(forms.Form):
    """The document behind a "已验证" badge.

    Three typed fields beside the file, and no more. An NNC1 also carries every
    director's name, residential address and identity-document number; CLAUDE.md
    rule 5 says take only what the question needs, and the question is "was this
    reviewer a client of this company", which these three answer.

    ``declared_secretary_name`` exists so a moderator can be shown a comparison
    against the official register before opening anything. It is typed by the
    person asking to be verified, so agreeing with the register proves nothing -
    see ``matching.py``. Disagreeing is what it is for.
    """

    #: What ``clean_document`` sniffed, for the view to hand to the service.
    inspected: InspectedUpload

    document = forms.FileField(
        label=_("NNC1 文件"),
        help_text=_(
            "支持 PDF、JPG、PNG，不超过 %(limit)s MB。文件存于私有存储，仅审核人员可查看，"
            "核验结束 %(days)s 日后自动删除，我们只保留核验结论与文件指纹。"
        )
        % {"limit": MAX_UPLOAD_BYTES // (1024 * 1024), "days": 90},
        widget=forms.ClearableFileInput(attrs={"class": "text-sm"}),
    )
    declared_company_name = forms.CharField(
        label=_("你的公司名称（NNC1 上的名称）"),
        max_length=255,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )
    declared_company_no = forms.CharField(
        label=_("公司编号（选填）"),
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )
    declared_secretary_name = forms.CharField(
        label=_("文件上列明的公司秘书名称"),
        max_length=255,
        help_text=_("请照抄 NNC1 上的写法，我们会与公司注册处的持牌名单比对。"),
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )

    def clean_document(self) -> Any:
        """Reject by real content type before anything is stored.

        The check has to happen here as well as in ``core.uploads`` because the
        form is what the person sees: an error next to the field beats a 500
        from the service layer.
        """
        upload = self.cleaned_data["document"]
        self.inspected = inspect_upload(upload)
        return upload
