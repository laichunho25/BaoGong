"""Requests for quotation, the quotes answering them, and the daily quota.

This is the only place on the platform where a buyer and a licensed company
meet before money changes hands, so three shapes here are load-bearing:

* **An RFQ carries a requirement, never a buyer.** There is no name, phone or
  email field on ``Rfq``. The wall is readable by every claimed company on the
  site, and a wall that publishes contact details is a lead list being
  scraped, not a marketplace. Companies answer with a ``Quote``; the platform
  carries the message.
* **``Rfq.structured`` is the agent's draft, not the requirement.** A1 writes
  there and the typed columns beside it are what matching, filtering and the
  comparison table read - and they are only filled once the buyer has confirmed
  the pre-filled form (CLAUDE.md rule 3, AI_AGENTS A1).
* **Quotes are stored in standard parts.** ``QuoteLineItem.label`` is a closed
  enum precisely so the comparison table can put two companies' prices on one
  basis. A free-text price list is a price list nobody can compare, which is
  the problem this product exists to solve.

``QuotaLedger`` is the free-tier rule made durable; the arithmetic that spends
it is in ``services.py``.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel
from apps.core.money import Money
from apps.providers.models import BankType, Provider, ServiceCategory


class RfqStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    OPEN = "open", _("Open for quotes")
    CLOSED = "closed", _("Closed by the buyer")
    AWARDED = "awarded", _("Awarded")
    EXPIRED = "expired", _("Expired")


#: The states in which a company may still spend a quote on an RFQ.
QUOTABLE_STATUSES = (RfqStatus.OPEN,)


class Visibility(models.TextChoices):
    PUBLIC = "public", _("Visible on the request wall")
    INVITED_ONLY = "invited_only", _("Invited companies only")


class CompanyType(models.TextChoices):
    HK_PRIVATE_LIMITED = "hk_private_limited", _("Hong Kong private limited company")
    HK_BRANCH = "hk_branch", _("Hong Kong branch of a non-HK company")
    HK_REPRESENTATIVE_OFFICE = "hk_rep_office", _("Representative office")
    OFFSHORE = "offshore", _("Offshore company")
    UNDECIDED = "undecided", _("Not decided yet")


class Timeline(models.TextChoices):
    ASAP = "asap", _("As soon as possible")
    WITHIN_1_MONTH = "within_1_month", _("Within a month")
    WITHIN_3_MONTHS = "within_3_months", _("Within three months")
    FLEXIBLE = "flexible", _("Flexible")
    UNDECIDED = "undecided", _("Not decided yet")


class Rfq(BaseModel):
    """One buyer's requirement, written once and answered many times.

    Kept deliberately thin on identity: the buyer FK is the only link to a
    person, and no view may render it to anybody but the buyer and the
    moderation team.
    """

    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="rfqs"
    )
    title = models.CharField(max_length=140)
    raw_input = models.TextField(
        blank=True, help_text="The buyer's own words, before A1 structured them."
    )
    # Advisory only. Never read by matching or by the comparison table; kept so
    # a bad extraction can be compared against what the buyer confirmed.
    structured = models.JSONField(
        default=dict, blank=True, help_text="A1 draft (CLAUDE.md rule 3). Never authoritative."
    )
    is_ai_assisted = models.BooleanField(
        default=False, help_text="True when the form the buyer confirmed was pre-filled by A1."
    )
    # A2's shortlist, shown to the buyer as suggestions to look at. Advisory in
    # the same sense as ``structured``: it decides nothing, gives no company any
    # standing on this requirement, and every company on the wall may still
    # quote whether or not it appears here (CLAUDE.md rule 3).
    matches = models.JSONField(
        default=dict, blank=True, help_text="A2 suggestions. Never a permission and never a rank."
    )

    company_type = models.CharField(
        max_length=24, choices=CompanyType.choices, default=CompanyType.HK_PRIVATE_LIMITED
    )
    shareholder_nationalities = ArrayField(
        models.CharField(max_length=2), default=list, blank=True, help_text="ISO 3166-1 alpha-2."
    )
    business_nature = models.CharField(max_length=200, blank=True)
    services_needed = ArrayField(
        models.CharField(max_length=32, choices=ServiceCategory.choices), default=list
    )
    needs_bank_account = models.BooleanField(default=False)
    preferred_banks = ArrayField(
        models.CharField(max_length=16, choices=BankType.choices), default=list, blank=True
    )

    # CLAUDE.md rule 6. A budget is optional: plenty of buyers have no idea what
    # the work costs, which is the reason they are asking.
    currency = models.CharField(max_length=3, default="HKD")
    budget_min_minor = models.BigIntegerField(null=True, blank=True)
    budget_max_minor = models.BigIntegerField(null=True, blank=True)
    timeline = models.CharField(max_length=16, choices=Timeline.choices, default=Timeline.UNDECIDED)

    status = models.CharField(
        max_length=16, choices=RfqStatus.choices, default=RfqStatus.DRAFT, db_index=True
    )
    visibility = models.CharField(
        max_length=16, choices=Visibility.choices, default=Visibility.PUBLIC
    )
    published_at = models.DateTimeField(null=True, blank=True)
    # Never null once open: companies pay for the right to answer, so a wall of
    # requests nobody is waiting on any more spends a scarce daily quota on
    # nothing. Set by services.publish_rfq.
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=200, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("RFQ")
        verbose_name_plural = _("RFQs")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(budget_min_minor__isnull=True)
                | models.Q(budget_max_minor__isnull=True)
                | models.Q(budget_max_minor__gte=models.F("budget_min_minor")),
                name="rfq_budget_range_ordered",
            ),
            models.CheckConstraint(
                condition=models.Q(budget_min_minor__isnull=True)
                | models.Q(budget_min_minor__gte=0),
                name="rfq_budget_min_not_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "visibility", "-published_at"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status == RfqStatus.OPEN and not self.has_expired

    @property
    def has_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def budget_min(self) -> Money | None:
        if self.budget_min_minor is None:
            return None
        return Money(self.budget_min_minor, self.currency)

    @property
    def budget_max(self) -> Money | None:
        if self.budget_max_minor is None:
            return None
        return Money(self.budget_max_minor, self.currency)

    @property
    def service_labels(self) -> list[str]:
        """Human labels for the ArrayField; ``get_FOO_display`` does not apply."""
        labels = dict(ServiceCategory.choices)
        return [str(labels[value]) for value in self.services_needed if value in labels]


class QuoteStatus(models.TextChoices):
    SUBMITTED = "submitted", _("Submitted")
    SHORTLISTED = "shortlisted", _("Shortlisted")
    ACCEPTED = "accepted", _("Accepted")
    DECLINED = "declined", _("Not selected")
    WITHDRAWN = "withdrawn", _("Withdrawn by the company")
    EXPIRED = "expired", _("Expired")


class Quote(BaseModel):
    """One company's answer, in fields a comparison table can line up.

    The totals are duplicated from the line items on purpose. A quote is an
    offer a company stands behind, and the number it typed is the number it is
    held to - recomputing the total from the parts every render would let a
    later change to the standard item list silently restate an old offer.
    """

    rfq = models.ForeignKey(Rfq, on_delete=models.CASCADE, related_name="quotes")
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="quotes")
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="submitted_quotes"
    )

    currency = models.CharField(max_length=3, default="HKD")
    first_year_total_minor = models.BigIntegerField()
    # Nullable: for one-off work there is nothing to renew, and a zero there
    # would read as "renewal is free" in the comparison table.
    renewal_total_minor = models.BigIntegerField(null=True, blank=True)
    includes_govt_fee = models.BooleanField(
        default=False, help_text="Whether the first-year total already contains government fees."
    )
    delivery_days = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Working days to delivery, as promised by the company."
    )
    validity_days = models.PositiveSmallIntegerField(default=14)
    message = models.TextField(blank=True)

    # A5 output. Advisory: shown beside the quote as questions to ask, never as
    # a verdict, and the price percentile in it is computed in SQL, not by a
    # model (AI_AGENTS A5).
    analysis = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=16, choices=QuoteStatus.choices, default=QuoteStatus.SUBMITTED, db_index=True
    )
    submitted_at = models.DateTimeField(default=timezone.now, db_index=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        constraints = [
            # One live quote per company per request. Revising a price is a
            # withdrawal followed by a new quote, so the buyer never has two
            # different first-year totals from the same company on screen.
            models.UniqueConstraint(
                fields=["rfq", "provider"],
                condition=~models.Q(status="withdrawn"),
                name="rfq_one_live_quote_per_provider",
            ),
            models.CheckConstraint(
                condition=models.Q(first_year_total_minor__gte=0),
                name="rfq_quote_first_year_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(renewal_total_minor__isnull=True)
                | models.Q(renewal_total_minor__gte=0),
                name="rfq_quote_renewal_not_negative",
            ),
        ]
        indexes = [models.Index(fields=["provider", "-submitted_at"])]

    def __str__(self) -> str:
        return f"{self.provider.slug} -> {self.rfq_id} ({self.status})"

    @property
    def first_year_total(self) -> Money:
        return Money(self.first_year_total_minor, self.currency)

    @property
    def renewal_total(self) -> Money | None:
        if self.renewal_total_minor is None:
            return None
        return Money(self.renewal_total_minor, self.currency)

    @property
    def valid_until(self) -> object:
        return self.submitted_at + timedelta(days=self.validity_days)

    @property
    def is_live(self) -> bool:
        return self.status in (QuoteStatus.SUBMITTED, QuoteStatus.SHORTLISTED, QuoteStatus.ACCEPTED)


class LineItemLabel(models.TextChoices):
    """The standard basis for comparison.

    Closed on purpose. Every label a company can add is one the platform knows
    how to line up against the same label from another company; ``OTHER`` is
    the escape hatch and is excluded from "is this item missing?" checks.
    """

    GOVT_INCORPORATION_FEE = "govt_incorporation_fee", _("Government incorporation fee")
    BUSINESS_REGISTRATION_FEE = "business_registration_fee", _("Business registration fee")
    INCORPORATION_SERVICE = "incorporation_service", _("Incorporation service fee")
    COMPANY_SECRETARY = "company_secretary", _("Company secretary (first year)")
    REGISTERED_ADDRESS = "registered_address", _("Registered address (first year)")
    COMPANY_KIT = "company_kit", _("Company kit and chops")
    BANK_ACCOUNT_ASSIST = "bank_account_assist", _("Bank account assistance")
    ANNUAL_RETURN = "annual_return", _("Annual return filing")
    ACCOUNTING = "accounting", _("Accounting")
    AUDIT_LIAISON = "audit_liaison", _("Audit liaison")
    COURIER = "courier", _("Courier and handling")
    OTHER = "other", _("Other")


class LineItemUnit(models.TextChoices):
    ONE_OFF = "one_off", _("One-off")
    YEARLY = "yearly", _("Per year")
    MONTHLY = "monthly", _("Per month")
    HOURLY = "hourly", _("Per hour")
    ESTIMATE = "estimate", _("Estimate only")


class QuoteLineItem(BaseModel):
    """One priced part of a quote."""

    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="line_items")
    label = models.CharField(max_length=32, choices=LineItemLabel.choices)
    custom_label = models.CharField(
        max_length=120, blank=True, help_text="Only meaningful when label is 'other'."
    )
    amount_minor = models.BigIntegerField()
    unit = models.CharField(
        max_length=16, choices=LineItemUnit.choices, default=LineItemUnit.ONE_OFF
    )
    is_optional = models.BooleanField(
        default=False, help_text="Excluded from the first-year total when true."
    )
    note = models.CharField(max_length=200, blank=True)
    ordinal = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["ordinal", "label"]
        constraints = [
            # A standard label twice in one quote makes the comparison column
            # ambiguous; 'other' may repeat because that is what it is for.
            models.UniqueConstraint(
                fields=["quote", "label"],
                condition=~models.Q(label="other"),
                name="rfq_one_line_item_per_standard_label",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_minor__gte=0), name="rfq_line_item_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label} {self.amount_minor}"

    @property
    def amount(self) -> Money:
        """The amount in its quote's currency.

        Reads ``quote.currency`` rather than carrying a currency column: a line
        item priced in a different currency from its own quote is not a case
        worth supporting, it is a bug worth making impossible.
        """
        return Money(self.amount_minor, self.quote.currency)

    @property
    def display_label(self) -> str:
        if self.label == LineItemLabel.OTHER and self.custom_label:
            return self.custom_label
        return str(dict(LineItemLabel.choices).get(self.label, self.label))


class QuotaLedger(BaseModel):
    """What one company spent answering requests on one day.

    A running balance rather than a counter: ``free_used`` and ``paid_used``
    are that day's spend, and ``paid_balance`` is what is left of the purchased
    credits after it. Rows are created only by spending or by buying, and a new
    row carries the previous balance forward (``services._ledger_for``), so the
    table doubles as the statement a company can be shown when it asks why it
    could not answer a request.
    """

    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="quota_ledger")
    date = models.DateField(db_index=True)
    free_used = models.PositiveSmallIntegerField(default=0)
    paid_used = models.PositiveIntegerField(default=0)
    paid_balance = models.PositiveIntegerField(
        default=0, help_text="Purchased quotes remaining at the end of this day."
    )

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["provider", "date"], name="rfq_one_ledger_row_per_day")
        ]

    def __str__(self) -> str:
        return f"{self.provider.slug} {self.date} free={self.free_used} paid={self.paid_used}"

    # No ``free_remaining`` here on purpose: how much is left depends on the
    # company's tier and, for the free tier, on the other rows of the same
    # month. One row cannot answer that question - ``rfq.allowances`` and
    # ``selectors.quota_state`` can.
