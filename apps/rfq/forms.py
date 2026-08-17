"""Forms for the matching layer.

Validation only; ``views.py`` hands the cleaned data to ``services.py``
(ARCHITECTURE section 3).

Two decisions worth stating:

* **Money is typed in major units and stored in minor ones.** Nobody types
  ``680000`` for HKD 6,800, so the forms take a ``Decimal`` and convert through
  ``Money.from_decimal``, which refuses excess precision rather than rounding it
  away (CLAUDE.md rule 6).
* **A quote's parts are a formset over a closed label list.** The comparison
  table can only line two companies up if they used the same labels, so the
  label is a select and not a text box - the one field on this page a company
  would most like to be free text is exactly the field that must not be.

There is no contact field anywhere in this module. The request wall carries a
requirement, not a buyer (COMPLIANCE section 4).
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.money import Money, MoneyError
from apps.providers.models import BankType, ServiceCategory
from apps.rfq.models import CompanyType, LineItemLabel, LineItemUnit, Timeline

INPUT_CLASSES = (
    "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm "
    "focus:border-slate-500 focus:outline-none"
)

#: The currencies a buyer or a company may quote in. Narrower than
#: ``core.money`` supports on purpose: every extra currency is another column
#: the comparison table cannot add up.
CURRENCY_CHOICES = [("HKD", "HKD"), ("CNY", "CNY"), ("USD", "USD")]

MIN_DESCRIPTION_LENGTH = 20


def _to_minor(value: Any, currency: str, label: str) -> int | None:
    """Major units from a form field to minor units, or None if left blank."""
    if value in (None, ""):
        return None
    try:
        return Money.from_decimal(value, currency).amount_minor
    except MoneyError as exc:
        raise forms.ValidationError(
            _("%(label)s：%(error)s") % {"label": label, "error": exc}
        ) from exc


class RfqForm(forms.Form):
    """One buyer's requirement.

    ``raw_input`` is the buyer's own words and stays on the record beside the
    typed fields: when A1 pre-fills this form (P5-3) the difference between what
    the buyer wrote and what the model made of it is the only way to see the
    model getting it wrong.
    """

    title = forms.CharField(
        label=_("需求标题"),
        max_length=140,
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASSES, "placeholder": _("例：注册香港有限公司 + 开公户")}
        ),
    )
    raw_input = forms.CharField(
        label=_("详细说明"),
        min_length=MIN_DESCRIPTION_LENGTH,
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 5}),
        help_text=_(
            "请写清楚业务性质、股东情况与时间要求，至少 %(n)s 字。"
            "请不要填写姓名、电话、微信等联系方式——秘书公司通过平台报价联系您。"
        )
        % {"n": MIN_DESCRIPTION_LENGTH},
    )
    company_type = forms.ChoiceField(
        label=_("公司类型"),
        choices=CompanyType.choices,
        widget=forms.Select(attrs={"class": INPUT_CLASSES}),
    )
    business_nature = forms.CharField(
        label=_("业务性质（选填）"),
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASSES, "placeholder": _("例：跨境电商、贸易、咨询")}
        ),
    )
    shareholder_nationalities = forms.CharField(
        label=_("股东国籍或地区（选填）"),
        max_length=120,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES, "placeholder": "CN, HK"}),
        help_text=_("填两位地区代码，用逗号分隔。银行开户的难易与这一项直接相关。"),
    )
    services_needed = forms.MultipleChoiceField(
        label=_("需要的服务"),
        choices=ServiceCategory.choices,
        widget=forms.CheckboxSelectMultiple,
    )
    needs_bank_account = forms.BooleanField(label=_("需要开立银行账户"), required=False)
    preferred_banks = forms.MultipleChoiceField(
        label=_("倾向的银行（选填）"),
        choices=BankType.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    timeline = forms.ChoiceField(
        label=_("时间要求"),
        choices=Timeline.choices,
        widget=forms.Select(attrs={"class": INPUT_CLASSES}),
    )
    currency = forms.ChoiceField(
        label=_("预算货币"),
        choices=CURRENCY_CHOICES,
        widget=forms.Select(attrs={"class": INPUT_CLASSES}),
    )
    budget_min = forms.DecimalField(
        label=_("预算下限（选填）"),
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASSES, "step": "0.01"}),
    )
    budget_max = forms.DecimalField(
        label=_("预算上限（选填）"),
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASSES, "step": "0.01"}),
    )

    def clean_shareholder_nationalities(self) -> list[str]:
        raw = self.cleaned_data.get("shareholder_nationalities", "")
        codes = [part.strip().upper() for part in raw.replace("，", ",").split(",") if part.strip()]
        if any(len(code) != 2 or not code.isalpha() for code in codes):
            raise forms.ValidationError(_("请使用两位地区代码，例如 CN、HK。"))
        return codes

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        currency = cleaned.get("currency", "HKD")
        cleaned["budget_min_minor"] = _to_minor(
            cleaned.get("budget_min"), currency, str(_("预算下限"))
        )
        cleaned["budget_max_minor"] = _to_minor(
            cleaned.get("budget_max"), currency, str(_("预算上限"))
        )
        return cleaned

    def to_service_kwargs(self) -> dict[str, Any]:
        """The cleaned data in the shape ``services.create_rfq`` takes."""
        data = self.cleaned_data
        return {
            "title": data["title"],
            "raw_input": data["raw_input"],
            "services_needed": data["services_needed"],
            "company_type": data["company_type"],
            "business_nature": data["business_nature"],
            "shareholder_nationalities": data["shareholder_nationalities"],
            "needs_bank_account": data["needs_bank_account"],
            "preferred_banks": data["preferred_banks"],
            "timeline": data["timeline"],
            "currency": data["currency"],
            "budget_min_minor": data["budget_min_minor"],
            "budget_max_minor": data["budget_max_minor"],
        }


class QuoteForm(forms.Form):
    """One company's answer.

    ``first_year_total`` is asked for separately from the line items rather than
    summed from them: the total is the number the company is held to, and a
    company that leaves an item out of the breakdown should not thereby quote a
    lower price than it meant to.
    """

    currency = forms.ChoiceField(
        label=_("货币"),
        choices=CURRENCY_CHOICES,
        widget=forms.Select(attrs={"class": INPUT_CLASSES}),
    )
    first_year_total = forms.DecimalField(
        label=_("首年总费用"),
        min_value=0,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASSES, "step": "0.01"}),
    )
    renewal_total = forms.DecimalField(
        label=_("次年续期总费用（选填）"),
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASSES, "step": "0.01"}),
        help_text=_("留空表示该项不适用；填 0 表示续期免费。"),
    )
    includes_govt_fee = forms.BooleanField(label=_("首年总费用已包含政府费用"), required=False)
    delivery_days = forms.IntegerField(
        label=_("承诺交付工作日（选填）"),
        required=False,
        min_value=0,
        max_value=365,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASSES}),
    )
    validity_days = forms.IntegerField(
        label=_("报价有效期（日）"),
        min_value=1,
        max_value=90,
        initial=14,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASSES}),
    )
    message = forms.CharField(
        label=_("给客户的说明（选填）"),
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"class": INPUT_CLASSES, "rows": 4}),
        help_text=_("不要在这里承诺开户成功率或使用「保证」「包过」等字眼。"),
    )

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        currency = cleaned.get("currency", "HKD")
        cleaned["first_year_total_minor"] = _to_minor(
            cleaned.get("first_year_total"), currency, str(_("首年总费用"))
        )
        cleaned["renewal_total_minor"] = _to_minor(
            cleaned.get("renewal_total"), currency, str(_("次年续期总费用"))
        )
        return cleaned

    def to_service_kwargs(self) -> dict[str, Any]:
        data = self.cleaned_data
        return {
            "currency": data["currency"],
            "first_year_total_minor": data["first_year_total_minor"],
            "renewal_total_minor": data["renewal_total_minor"],
            "includes_govt_fee": data["includes_govt_fee"],
            "delivery_days": data["delivery_days"],
            "validity_days": data["validity_days"],
            "message": data["message"],
        }


class QuoteLineItemForm(forms.Form):
    """One priced part of a quote. Every field optional; a blank row is skipped."""

    label = forms.ChoiceField(
        label=_("项目"),
        choices=[("", _("（不填）")), *LineItemLabel.choices],
        required=False,
        widget=forms.Select(attrs={"class": INPUT_CLASSES}),
    )
    custom_label = forms.CharField(
        label=_("其他项目名称"),
        max_length=120,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )
    amount = forms.DecimalField(
        label=_("金额"),
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASSES, "step": "0.01"}),
    )
    unit = forms.ChoiceField(
        label=_("计费方式"),
        choices=LineItemUnit.choices,
        required=False,
        initial=LineItemUnit.ONE_OFF,
        widget=forms.Select(attrs={"class": INPUT_CLASSES}),
    )
    is_optional = forms.BooleanField(label=_("可选项，不计入首年总额"), required=False)
    note = forms.CharField(
        label=_("备注"),
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASSES}),
    )

    @property
    def is_blank(self) -> bool:
        return not self.cleaned_data.get("label") and self.cleaned_data.get("amount") is None

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        label, amount = cleaned.get("label"), cleaned.get("amount")
        if not label and amount is None:
            return cleaned
        if not label:
            raise forms.ValidationError(_("填了金额就要选择对应项目。"))
        if amount is None:
            raise forms.ValidationError(_("选了项目就要填写金额。"))
        if label == LineItemLabel.OTHER and not cleaned.get("custom_label", "").strip():
            raise forms.ValidationError(_("选择「其他」时请填写项目名称。"))
        return cleaned


QuoteLineItemFormSet = forms.formset_factory(QuoteLineItemForm, extra=6, can_delete=False)


def line_items_from(formset: Any, currency: str) -> list[dict[str, Any]]:
    """Turn a validated formset into the list ``submit_quote`` takes.

    Blank rows disappear here rather than in the service: an empty row is a
    person who did not fill something in, which is a form concern, while a row
    with a label the comparison table cannot use is a rule and stays in
    ``services._write_line_items``.
    """
    items: list[dict[str, Any]] = []
    for form in formset:
        if not form.cleaned_data or form.is_blank:
            continue
        data = form.cleaned_data
        items.append(
            {
                "label": data["label"],
                "custom_label": data.get("custom_label", ""),
                "amount_minor": Money.from_decimal(data["amount"], currency).amount_minor,
                "unit": data.get("unit") or LineItemUnit.ONE_OFF,
                "is_optional": data.get("is_optional", False),
                "note": data.get("note", ""),
            }
        )
    return items
