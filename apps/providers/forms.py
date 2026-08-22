"""Forms for the claim flow and for a company editing its own page.

Validation only - the view hands cleaned data to ``services.py``
(ARCHITECTURE section 3). The uploads are inspected here so that a bad file is
reported next to the field that produced it, but the inspection itself lives in
``core.uploads`` because P4's NNC1 upload needs exactly the same checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.form_styles import CHECKBOX_CLASSES, FILE_CLASSES, INPUT_CLASSES
from apps.core.uploads import (
    IMAGE_CONTENT_TYPES,
    MAX_LOGO_BYTES,
    MAX_UPLOAD_BYTES,
    InspectedUpload,
    inspect_upload,
)
from apps.core.validators import PHONE_INPUT_ATTRS, normalise_phone, validate_phone
from apps.providers.models import BankType, EvidenceKind, Language, ServiceCategory

if TYPE_CHECKING:
    from collections.abc import Collection

#: Enough for a BR certificate, an address proof and a letter of authorisation.
MAX_EVIDENCE_FILES = 5


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """A file field that keeps every selected file.

    ``forms.FileField`` is written for one file: its widget hands back a list
    once ``allow_multiple_selected`` is on, and the plain field then reports
    "no file was submitted" because a list has no ``.name``. Cleaning each
    member individually keeps the per-file checks Django does for free.
    """

    widget = MultipleFileInput

    def clean(self, data: Any, initial: Any = None) -> list[Any]:
        clean_one = super().clean
        if isinstance(data, list | tuple):
            return [clean_one(item, initial) for item in data]
        return [clean_one(data, initial)]


class ClaimSubmissionForm(forms.Form):
    """What a company tells us when it asks for control of its page."""

    contact_name = forms.CharField(
        label=_("联系人姓名"),
        max_length=120,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES, "autocomplete": "name"}),
    )
    contact_role = forms.CharField(
        label=_("职务"),
        max_length=120,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )
    contact_phone = forms.CharField(
        label=_("联系电话"),
        max_length=32,
        required=False,
        validators=[validate_phone],
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASSES, "autocomplete": "tel", **PHONE_INPUT_ATTRS}
        ),
    )
    business_registration_no = forms.CharField(
        label=_("商业登记号码（BR）"),
        max_length=32,
        required=False,
        help_text=_("官方持牌名单不含 BR 号码，此项仅供审核人核对，不会公开显示。"),
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )
    website = forms.URLField(
        label=_("公司网站"),
        required=False,
        # Django 6 changes this default; stating it keeps "example.com" meaning
        # the same thing before and after the upgrade.
        assume_scheme="https",
        help_text=_("用于网站所有权验证（DNS TXT 或网页 meta 标签）。没有网站可留空。"),
        widget=forms.URLInput(attrs={"class": INPUT_CLASSES, "placeholder": "https://"}),
    )
    evidence_kind = forms.ChoiceField(
        label=_("证明文件类型"),
        choices=EvidenceKind.choices,
        initial=EvidenceKind.BUSINESS_REGISTRATION,
        widget=forms.Select(attrs={"class": INPUT_CLASSES}),
    )
    evidence = MultipleFileField(
        label=_("证明文件"),
        help_text=_(
            "支持 PDF、JPG、PNG，单个文件不超过 %(limit)s MB，最多 %(count)s 个。"
            "文件将加密存放于私有存储，仅审核人员可查看，审核结束 90 日后自动删除。"
        )
        % {"limit": MAX_UPLOAD_BYTES // (1024 * 1024), "count": MAX_EVIDENCE_FILES},
        widget=MultipleFileInput(attrs={"class": FILE_CLASSES, "multiple": True}),
    )
    applicant_note = forms.CharField(
        label=_("补充说明（选填）"),
        required=False,
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 3}),
    )
    confirms_authority = forms.BooleanField(
        label=_("本人确认获该公司授权提交此申请，所提供资料真实无误。"),
        required=True,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: Populated by ``clean_evidence``; the view stores these with the files.
        self.inspected: list[InspectedUpload] = []

    def clean_contact_phone(self) -> str:
        """Keep the digits, drop the spaces and dashes somebody typed."""
        return normalise_phone(str(self.cleaned_data.get("contact_phone") or ""))

    def clean_evidence(self) -> list[Any]:
        """Inspect every uploaded file before anything is stored."""
        uploads: list[Any] = self.cleaned_data["evidence"]
        if not uploads:
            raise ValidationError(_("请至少上传一份证明文件。"))
        if len(uploads) > MAX_EVIDENCE_FILES:
            raise ValidationError(_("最多上传 %(count)s 个文件。") % {"count": MAX_EVIDENCE_FILES})

        inspected = []
        for upload in uploads:
            # inspect_upload raises ValidationError with a message that names
            # the accepted formats without saying how this file was classified.
            inspected.append(inspect_upload(upload))
        self.inspected = inspected
        return uploads


MAX_DESCRIPTION_CHARS = 1200
MAX_SPECIALTIES = 8


class ProviderProfileForm(forms.Form):
    """What a company may change about its own page.

    Every field this platform lets a company edit is declared here, and the
    constructor then removes the ones its tier or its licence does not permit
    (``services.editable_fields``). Building the form from the permission,
    rather than hiding fields in the template, means a hand-crafted POST cannot
    set a field the company is not paying for - the field simply does not exist
    on the form that cleans it.
    """

    contact_email = forms.EmailField(
        label=_("联系邮箱"),
        required=False,
        widget=forms.EmailInput(attrs={"class": INPUT_CLASSES, "autocomplete": "email"}),
    )
    contact_phone = forms.CharField(
        label=_("联系电话"),
        max_length=32,
        required=False,
        validators=[validate_phone],
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASSES, "autocomplete": "tel", **PHONE_INPUT_ATTRS}
        ),
    )
    contact_wechat = forms.CharField(
        label=_("微信号"),
        max_length=64,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )
    website = forms.URLField(
        label=_("公司网站"),
        required=False,
        assume_scheme="https",
        help_text=_("更换网址后需要重新完成一次网站所有权验证。"),
        widget=forms.URLInput(attrs={"class": INPUT_CLASSES, "placeholder": "https://"}),
    )
    service_categories = forms.MultipleChoiceField(
        label=_("业务范畴"),
        choices=ServiceCategory.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": CHECKBOX_CLASSES}),
    )
    description = forms.CharField(
        label=_("公司简介"),
        required=False,
        max_length=MAX_DESCRIPTION_CHARS,
        help_text=_(
            "简介会以贵公司的名义刊登在页面上，提交后需经我们审核才会显示，"
            "一般 3 个工作日内处理。请勿写入开户成功率或任何官方背书的说法。"
        ),
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 8}),
    )
    founded_year = forms.IntegerField(
        label=_("成立年份"),
        required=False,
        min_value=1900,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASSES}),
    )
    team_size = forms.IntegerField(
        label=_("团队人数"),
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASSES}),
    )
    languages = forms.MultipleChoiceField(
        label=_("服务语言"),
        choices=Language.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": CHECKBOX_CLASSES}),
    )
    supports_simplified = forms.BooleanField(label=_("提供简体中文服务"), required=False)
    remote_onboarding = forms.BooleanField(label=_("可全程远程办理"), required=False)
    bank_account_support = forms.BooleanField(label=_("协助开立银行账户"), required=False)
    bank_types = forms.MultipleChoiceField(
        label=_("合作银行类型"),
        choices=BankType.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": CHECKBOX_CLASSES}),
    )
    non_resident_shareholder_experience = forms.BooleanField(
        label=_("有非本地股东办理经验"), required=False
    )
    industry_specialties = forms.CharField(
        label=_("行业专长"),
        required=False,
        help_text=_("用逗号分隔，最多 %(count)s 项。") % {"count": MAX_SPECIALTIES},
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )

    def __init__(self, *args: Any, allowed: Collection[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name in list(self.fields):
            if name not in allowed:
                del self.fields[name]
        for name in (
            "supports_simplified",
            "remote_onboarding",
            "bank_account_support",
            "non_resident_shareholder_experience",
        ):
            if name in self.fields:
                self.fields[name].widget.attrs["class"] = CHECKBOX_CLASSES

    def clean_contact_phone(self) -> str:
        """Keep the digits, drop the spaces and dashes somebody typed."""
        return normalise_phone(str(self.cleaned_data.get("contact_phone") or ""))

    def clean_industry_specialties(self) -> list[str]:
        raw = self.cleaned_data.get("industry_specialties", "")
        items = [part.strip() for part in raw.replace("，", ",").split(",") if part.strip()]
        if len(items) > MAX_SPECIALTIES:
            raise ValidationError(_("最多填写 %(count)s 项行业专长。") % {"count": MAX_SPECIALTIES})
        return items

    def changed_values(self) -> dict[str, Any]:
        """Cleaned data keyed by model field, ready for ``apply_profile_edit``.

        Only fields the form actually carries are returned, so a tier that
        cannot edit a field never sends a value for it - not even its current
        one, which would otherwise show up in the change log as a no-op.
        """
        return {name: self.cleaned_data[name] for name in self.fields}


class ProviderLogoForm(forms.Form):
    """One image, on its way to the review queue.

    Kept apart from ``ProviderProfileForm`` rather than added as a field to it:
    a logo is not applied when the form is saved. It is scanned, then read by a
    moderator, and only then published - so it has a different lifecycle, a
    different failure mode, and a different sentence to say to the company.
    """

    logo = forms.FileField(
        label=_("公司标志"),
        help_text=_(
            "支持 JPG、PNG，不超过 %(limit)s MB。上传后需经我们审核才会显示在页面上；"
            "标志内不得含有开户成功率、官方背书等说法。"
        )
        % {"limit": MAX_LOGO_BYTES // (1024 * 1024)},
        widget=forms.ClearableFileInput(attrs={"class": FILE_CLASSES, "accept": "image/*"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: Set by ``clean_logo``; the view passes it to ``services.upload_logo``.
        self.inspected: InspectedUpload | None = None

    def clean_logo(self) -> Any:
        upload = self.cleaned_data["logo"]
        # Images only, and a tighter size limit than evidence: this file is
        # downloaded by every visitor of the page, not opened once by a
        # moderator.
        self.inspected = inspect_upload(
            upload, allowed=IMAGE_CONTENT_TYPES, max_bytes=MAX_LOGO_BYTES
        )
        return upload
