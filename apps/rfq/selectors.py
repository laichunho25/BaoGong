"""Reads for the matching layer.

The request wall is the query that matters. It answers "what may this company
see" rather than "what exists": a draft is nobody's business, an expired
request is not worth spending a quote on, and an invite-only request is not on
the wall at all. Nothing here writes, so a company browsing the wall can never
be charged for having looked.

``comparison_rows`` is the other half of the product promise. Two quotes are
comparable only if their parts are lined up by the same labels, including the
labels one of them left out - a blank cell that says "not quoted" is the whole
point, and it is the cell a free-text price list can never produce.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from django.db.models import Count, Q
from django.utils import timezone

from apps.core.money import Money
from apps.providers.models import ClaimStatus, Provider
from apps.rfq.models import (
    LineItemLabel,
    QuotaLedger,
    Quote,
    QuoteStatus,
    Rfq,
    RfqStatus,
    Visibility,
)

if TYPE_CHECKING:
    from datetime import date

    from django.db.models import QuerySet

    from apps.accounts.models import User

#: Everything a first-year comparison is expected to price. ``OTHER`` is not
#: here: an item only one company invented is not a gap in anyone else's quote.
STANDARD_LINE_ITEMS = tuple(label for label in LineItemLabel.values if label != LineItemLabel.OTHER)

#: Quote states a buyer is still choosing between.
LIVE_QUOTE_STATUSES = (QuoteStatus.SUBMITTED, QuoteStatus.SHORTLISTED, QuoteStatus.ACCEPTED)


def open_rfqs() -> QuerySet[Rfq]:
    """The request wall: open, public, and not past its deadline.

    Expiry is filtered here as well as swept by the beat task, so the wall is
    right between sweeps rather than right once a day.
    """
    return (
        Rfq.objects.filter(
            status=RfqStatus.OPEN,
            visibility=Visibility.PUBLIC,
            expires_at__gt=timezone.now(),
        )
        .annotate(quote_count=Count("quotes", filter=Q(quotes__status__in=LIVE_QUOTE_STATUSES)))
        .order_by("-published_at")
    )


def get_rfq(rfq_id: str) -> Rfq | None:
    return Rfq.objects.filter(pk=rfq_id).select_related("buyer").first()


def quotable_providers(user: User) -> list[Provider]:
    """The companies this user may answer a request for, right now.

    Same three conditions ``services._check_may_quote`` enforces - member,
    claimed, still on the register - asked ahead of time so a page can decline
    to show a button rather than show one that always fails. The service keeps
    checking anyway: this is what to render, not what is allowed.
    """
    from apps.accounts.permissions import member_providers

    ids = member_providers(user)
    if not ids:
        return []
    claimed = (
        Provider.objects.filter(pk__in=ids, claim_status=ClaimStatus.CLAIMED)
        .select_related("licensee")
        .order_by("slug")
    )
    # ``is_on_register`` follows the licensee row and is not a column, so the
    # last condition is filtered here rather than in SQL.
    return [provider for provider in claimed if provider.is_on_register]


def rfqs_for_buyer(buyer: User) -> QuerySet[Rfq]:
    """Everything the buyer has written, drafts included."""
    return Rfq.objects.filter(buyer=buyer).annotate(
        quote_count=Count("quotes", filter=Q(quotes__status__in=LIVE_QUOTE_STATUSES))
    )


def quotes_for_rfq(rfq: Rfq) -> QuerySet[Quote]:
    """The offers the buyer is choosing between, cheapest first."""
    return (
        Quote.objects.filter(rfq=rfq, status__in=LIVE_QUOTE_STATUSES)
        .select_related("provider", "provider__licensee")
        .prefetch_related("line_items")
        .order_by("first_year_total_minor")
    )


def quotes_for_provider(provider: Provider) -> QuerySet[Quote]:
    return Quote.objects.filter(provider=provider).select_related("rfq").order_by("-submitted_at")


def has_quoted(*, rfq: Rfq, provider: Provider) -> bool:
    """Whether the company already has a live offer on this request."""
    return Quote.objects.filter(rfq=rfq, provider=provider, status__in=LIVE_QUOTE_STATUSES).exists()


def todays_ledger(provider: Provider, day: date | None = None) -> QuotaLedger | None:
    """The company's quota row, or None if it has not spent anything today.

    Returns None rather than creating a row: reading a page must not write one,
    or every company that ever loaded the wall would have a ledger entry for
    every day it did so.
    """
    return QuotaLedger.objects.filter(provider=provider, date=day or timezone.localdate()).first()


class QuotaState(NamedTuple):
    """What the company has left today, safe to render without writing."""

    free_remaining: int
    paid_balance: int

    @property
    def can_quote(self) -> bool:
        return self.free_remaining > 0 or self.paid_balance > 0


def quota_state(provider: Provider, day: date | None = None) -> QuotaState:
    from django.conf import settings

    ledger = todays_ledger(provider, day)
    if ledger is None:
        # No row today. The balance still carries forward from the last day the
        # company spent or bought anything.
        previous = (
            QuotaLedger.objects.filter(provider=provider, date__lt=day or timezone.localdate())
            .order_by("-date")
            .first()
        )
        return QuotaState(
            free_remaining=settings.RFQ_FREE_QUOTES_PER_DAY,
            paid_balance=previous.paid_balance if previous else 0,
        )
    return QuotaState(free_remaining=ledger.free_remaining, paid_balance=ledger.paid_balance)


class ComparisonCell(NamedTuple):
    quote_id: str
    amount_minor: int | None
    is_optional: bool
    note: str
    #: Carried on the cell so a template can render the amount without knowing
    #: which quote the column belongs to.
    currency: str = "HKD"

    @property
    def display(self) -> str:
        """The amount as text, or empty for "this company did not price it"."""
        if self.amount_minor is None:
            return ""
        return Money(self.amount_minor, self.currency).format()


class ComparisonRow(NamedTuple):
    label: str
    display_label: str
    cells: list[ComparisonCell]

    @property
    def quoted_by_all(self) -> bool:
        return all(cell.amount_minor is not None for cell in self.cells)


def comparison_rows(quotes: list[Quote]) -> list[ComparisonRow]:
    """One row per standard item, one cell per quote, gaps included.

    A missing cell is information - "this company did not price the government
    fee" is exactly what a buyer comparing a cheap quote against an expensive
    one needs to see - so rows are built from the standard list rather than
    from whichever items happen to have been filled in.
    """
    by_quote: dict[str, dict[str, tuple[int, bool, str]]] = {
        str(quote.pk): {
            item.label: (item.amount_minor, item.is_optional, item.note)
            for item in quote.line_items.all()
        }
        for quote in quotes
    }

    labels = dict(LineItemLabel.choices)
    used = {label for items in by_quote.values() for label in items}
    rows: list[ComparisonRow] = []
    for label in STANDARD_LINE_ITEMS:
        if label not in used:
            # Nobody priced it; an empty row for every company teaches the
            # buyer nothing and makes the table unreadable.
            continue
        cells = []
        for quote in quotes:
            found = by_quote[str(quote.pk)].get(label)
            cells.append(
                ComparisonCell(
                    quote_id=str(quote.pk),
                    amount_minor=found[0] if found else None,
                    is_optional=bool(found[1]) if found else False,
                    note=found[2] if found else "",
                    currency=quote.currency,
                )
            )
        rows.append(ComparisonRow(label=label, display_label=str(labels[label]), cells=cells))
    return rows


def missing_standard_items(quote: Quote) -> list[str]:
    """Standard items this quote does not price.

    The rule-based half of A5: the fallback for "what is this quote not telling
    you" is a set difference, and it works with the model switched off.
    """
    priced = {item.label for item in quote.line_items.all()}
    return [label for label in STANDARD_LINE_ITEMS if label not in priced]
