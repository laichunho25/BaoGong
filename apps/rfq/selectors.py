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

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, NamedTuple

from django.db.models import Aggregate, Count, Exists, FloatField, OuterRef, Q
from django.utils import timezone

from apps.core.money import Money
from apps.providers.models import ClaimStatus, Provider
from apps.reviews.models import Review, ReviewStatus
from apps.rfq.models import (
    LineItemLabel,
    QuotaLedger,
    Quote,
    QuoteLineItem,
    QuoteStatus,
    Rfq,
    RfqStatus,
    Visibility,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date, datetime

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

    Requests from buyers who have had a review verified against a document sort
    first. That is the second half of the bargain the home page offers, and it
    is deliberately a *soft* preference: nothing is hidden, every open request
    is still on the wall, and the ordering is announced on the page that asks
    for the review rather than left as an undisclosed thumb on the scale.

    ``buyer_verified`` says something about the buyer, never who they are - the
    wall carries no name, and this annotation does not change that (COMPLIANCE
    section 4).
    """
    return (
        Rfq.objects.filter(
            status=RfqStatus.OPEN,
            visibility=Visibility.PUBLIC,
            expires_at__gt=timezone.now(),
        )
        .annotate(
            quote_count=Count("quotes", filter=Q(quotes__status__in=LIVE_QUOTE_STATUSES)),
            buyer_verified=Exists(
                Review.objects.filter(
                    author=OuterRef("buyer"),
                    status=ReviewStatus.PUBLISHED,
                    is_verified=True,
                )
            ),
        )
        .order_by("-buyer_verified", "-published_at")
    )


@dataclass(frozen=True, slots=True)
class MatchingSnapshot:
    """How busy the matching side is, in numbers safe to show anonymously.

    Counts only. The home page is public, and a requirement is only ever a
    requirement here - who wrote it, and anything that could identify them,
    stops at the login (COMPLIANCE section 4). The requirement cards
    themselves are rendered for signed-in visitors from ``open_rfqs``.
    """

    open_requests: int
    quotes_recently: int
    window_days: int
    free_quotes_per_day: int


def matching_snapshot(*, window_days: int = 30, now: datetime | None = None) -> MatchingSnapshot:
    from django.conf import settings

    moment = now or timezone.now()
    return MatchingSnapshot(
        open_requests=open_rfqs().count(),
        quotes_recently=Quote.objects.filter(
            submitted_at__gte=moment - timedelta(days=window_days)
        ).count(),
        window_days=window_days,
        free_quotes_per_day=settings.RFQ_FREE_QUOTES_PER_DAY,
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


class SuggestedMatch(NamedTuple):
    """One company A2 put on the buyer's reading list, with its sentences."""

    provider: Provider
    reasons: list[str]
    concerns: list[str]
    has_quoted: bool


def suggested_matches(rfq: Rfq) -> list[SuggestedMatch]:
    """``rfq.matches`` resolved back into companies, in the stored order.

    The stored suggestion holds slugs, not rows, so a company that was later
    struck off the register, unclaimed or deleted simply drops out here rather
    than being rendered from a stale copy of its name. That is also why the
    order is taken from the stored list and not from the database: the ranking
    is the suggestion's, and re-sorting it in SQL would quietly invent a
    different one.

    ``has_quoted`` is a fact about this request, not about the suggestion - a
    company already on the buyer's comparison table needs no second
    introduction.
    """
    items = (rfq.matches or {}).get("items") or []
    slugs = [str(item.get("provider_id", "")) for item in items if item.get("provider_id")]
    if not slugs:
        return []

    providers = {
        provider.slug: provider
        for provider in Provider.objects.filter(slug__in=slugs)
        .select_related("licensee")
        .prefetch_related("offerings")
    }
    quoted = set(
        Quote.objects.filter(rfq=rfq, status__in=LIVE_QUOTE_STATUSES).values_list(
            "provider__slug", flat=True
        )
    )

    matches: list[SuggestedMatch] = []
    for item in items:
        provider = providers.get(str(item.get("provider_id", "")))
        if provider is None or not provider.is_on_register:
            continue
        matches.append(
            SuggestedMatch(
                provider=provider,
                reasons=[str(reason) for reason in item.get("reasons") or []],
                concerns=[str(concern) for concern in item.get("concerns") or []],
                has_quoted=provider.slug in quoted,
            )
        )
    return matches


class _PercentileCont(Aggregate):
    """Postgres ``PERCENTILE_CONT(x) WITHIN GROUP (ORDER BY ...)``.

    Written out rather than approximated in Python because the whole point of
    AI_AGENTS A5 is that the market price is arithmetic the platform can show
    its working for, not something a model was asked to remember.
    """

    function = "PERCENTILE_CONT"
    template = "%(function)s(%(percentile)s) WITHIN GROUP (ORDER BY %(expressions)s)"
    output_field = FloatField()

    def __init__(self, expression: Any, percentile: float, **extra: Any) -> None:
        super().__init__(expression, percentile=percentile, **extra)


#: Below this many comparable prices, no percentile is published. "Cheaper than
#: the market" drawn from four quotes is not a fact about the market, and it is
#: shown to a buyer who is about to act on it.
MIN_PERCENTILE_SAMPLE = 8

#: The key under which the whole-quote percentile lives, beside the per-item
#: ones. Not a ``LineItemLabel`` value, and deliberately not confusable with one.
FIRST_YEAR_TOTAL = "first_year_total"


class PricePercentiles(NamedTuple):
    """What the market charges for one thing, in minor units."""

    p10: int
    p50: int
    p90: int
    sample_size: int


def market_percentiles(
    *, currency: str = "HKD", exclude_quote: Quote | None = None
) -> dict[str, PricePercentiles]:
    """Market prices per standard item, and per first-year total, from real quotes.

    Only live quotes in one currency: two currencies in one percentile is a
    number that means nothing, and a withdrawn or expired offer is not a price
    anybody can still get. The quote being analysed is excluded so that a quote
    is never compared against itself - with a small sample, including it drags
    the percentile towards the quote and hides exactly the outlier A5 exists to
    find.

    Entries below ``MIN_PERCENTILE_SAMPLE`` are dropped rather than returned
    with a caveat: a caller that has the number will use it.
    """
    quotes = Quote.objects.filter(status__in=LIVE_QUOTE_STATUSES, currency=currency)
    if exclude_quote is not None:
        quotes = quotes.exclude(pk=exclude_quote.pk)

    percentiles: dict[str, PricePercentiles] = {}

    items = (
        QuoteLineItem.objects.filter(quote__in=quotes)
        .exclude(label=LineItemLabel.OTHER)
        .values("label")
        .annotate(
            p10=_PercentileCont("amount_minor", 0.10),
            p50=_PercentileCont("amount_minor", 0.50),
            p90=_PercentileCont("amount_minor", 0.90),
            sample_size=Count("pk"),
        )
    )
    for row in items:
        if row["sample_size"] >= MIN_PERCENTILE_SAMPLE:
            percentiles[str(row["label"])] = _row_to_percentiles(row)

    totals = quotes.aggregate(
        p10=_PercentileCont("first_year_total_minor", 0.10),
        p50=_PercentileCont("first_year_total_minor", 0.50),
        p90=_PercentileCont("first_year_total_minor", 0.90),
        sample_size=Count("pk"),
    )
    if totals["sample_size"] >= MIN_PERCENTILE_SAMPLE:
        percentiles[FIRST_YEAR_TOTAL] = _row_to_percentiles(totals)
    return percentiles


def _row_to_percentiles(row: Mapping[str, Any]) -> PricePercentiles:
    """Round the float Postgres returns back to whole minor units.

    ``PERCENTILE_CONT`` interpolates, so it answers in fractions of a cent.
    Money is an integer on this platform (CLAUDE.md rule 6) and a percentile is
    a comparison threshold, so rounding here loses nothing.
    """
    return PricePercentiles(
        p10=round(row["p10"] or 0),
        p50=round(row["p50"] or 0),
        p90=round(row["p90"] or 0),
        sample_size=int(row["sample_size"]),
    )


def missing_standard_items(quote: Quote) -> list[str]:
    """Standard items this quote does not price.

    The rule-based half of A5: the fallback for "what is this quote not telling
    you" is a set difference, and it works with the model switched off.
    """
    priced = {item.label for item in quote.line_items.all()}
    return [label for label in STANDARD_LINE_ITEMS if label not in priced]
