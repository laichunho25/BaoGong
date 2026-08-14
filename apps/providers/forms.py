"""Forms for the claim flow.

Validation only - the view hands cleaned data to ``services.py``
(ARCHITECTURE section 3). The uploads are inspected here so that a bad file is
reported next to the field that produced it, but the inspection itself lives in
``core.uploads`` because P4's NNC1 upload needs exactly the same checks.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.uploads import MAX_UPLOAD_BYTES, InspectedUpload, inspect_upload
from apps.providers.models import EvidenceKind

INPUT_CLASSES = (
    "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm "
    "focus:border-slate-500 focus:outline-none"
)

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
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES, "autocomplete": "tel"}),
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
        widget=MultipleFileInput(attrs={"class": "text-sm", "multiple": True}),
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
