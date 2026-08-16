"""The matching layer's rules.

Grouped around the three things that would hurt if they broke: a request that
reaches companies before its author meant it to, a company answering in a name
it never proved was its own, and the free allowance - which is the product's
price list expressed as a number of database rows.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from django.utils import timezone

from apps.accounts.models import ProviderMember
from apps.providers.models import ClaimStatus
from apps.registry.models import LicenceStatus, allow_registry_writes
from apps.rfq import selectors, services
from apps.rfq.models import QuotaLedger, Quote, QuoteStatus, Rfq, RfqStatus, Visibility

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider

pytestmark = pytest.mark.django_db


# ------------------------------------------------------------------- requests


def test_a_new_request_is_a_draft_nobody_can_see(buyer: User) -> None:
    """A1 pre-fills this form, so the buyer confirms what a licensed company
    will read before anybody reads it (CLAUDE.md rule 3)."""
    rfq = services.create_rfq(
        buyer=buyer, title="Set up a HK company", services_needed=["incorporation"]
    )

    assert rfq.status == RfqStatus.DRAFT
    assert rfq.published_at is None
    assert list(selectors.open_rfqs()) == []


def test_a_request_with_no_services_is_refused(buyer: User) -> None:
    with pytest.raises(services.RfqError):
        services.create_rfq(buyer=buyer, title="Help", services_needed=[])


def test_an_untitled_request_is_refused(buyer: User) -> None:
    with pytest.raises(services.RfqError):
        services.create_rfq(buyer=buyer, title="   ", services_needed=["incorporation"])


def test_a_request_cannot_be_published_twice(open_rfq: Rfq, buyer: User) -> None:
    """Otherwise republishing would reset the deadline on a request companies
    have already paid to answer."""
    with pytest.raises(services.RfqError):
        services.publish_rfq(rfq=open_rfq, buyer=buyer)


def test_a_budget_that_runs_backwards_is_refused(buyer: User) -> None:
    with pytest.raises(services.RfqError):
        services.create_rfq(
            buyer=buyer,
            title="Set up a HK company",
            services_needed=["incorporation"],
            budget_min_minor=900_00,
            budget_max_minor=100_00,
        )


def test_a_currency_the_platform_cannot_price_is_refused(buyer: User) -> None:
    with pytest.raises(services.RfqError, match="Unsupported currency"):
        services.create_rfq(
            buyer=buyer,
            title="Set up a HK company",
            services_needed=["incorporation"],
            currency="XBT",
            budget_min_minor=100_00,
        )


def test_publishing_puts_a_deadline_on_it(buyer: User) -> None:
    """Companies spend a scarce daily quota to answer, so an open request has
    to stop being open by itself."""
    rfq = services.create_rfq(
        buyer=buyer, title="Set up a HK company", services_needed=["incorporation"]
    )

    published = services.publish_rfq(rfq=rfq, buyer=buyer)

    assert published.status == RfqStatus.OPEN
    assert published.expires_at is not None
    assert published.expires_at > timezone.now()
    assert list(selectors.open_rfqs()) == [published]


def test_an_unverified_buyer_cannot_publish(make_user: Callable[..., User]) -> None:
    stranger = make_user(email="unverified@example.com", verified=False)
    rfq = services.create_rfq(
        buyer=stranger, title="Set up a HK company", services_needed=["incorporation"]
    )

    with pytest.raises(services.RfqError):
        services.publish_rfq(rfq=rfq, buyer=stranger)


def test_only_the_author_may_publish_or_close(buyer: User, make_user: Callable[..., User]) -> None:
    rfq = services.create_rfq(
        buyer=buyer, title="Set up a HK company", services_needed=["incorporation"]
    )
    stranger = make_user(email="stranger@example.com")

    with pytest.raises(services.RfqError):
        services.publish_rfq(rfq=rfq, buyer=stranger)
    with pytest.raises(services.RfqError):
        services.close_rfq(rfq=rfq, buyer=stranger)


def test_an_expired_request_leaves_the_wall_even_before_the_sweep(open_rfq: Rfq) -> None:
    """The wall filters on the deadline itself: between two runs of the beat
    task, a dead request must not still be costing anybody a quote."""
    Rfq.objects.filter(pk=open_rfq.pk).update(expires_at=timezone.now() - timedelta(minutes=1))

    assert list(selectors.open_rfqs()) == []


def test_the_sweep_marks_them_expired(open_rfq: Rfq) -> None:
    Rfq.objects.filter(pk=open_rfq.pk).update(expires_at=timezone.now() - timedelta(days=1))

    assert services.expire_open_rfqs() == 1
    open_rfq.refresh_from_db()
    assert open_rfq.status == RfqStatus.EXPIRED


def test_an_invited_only_request_is_not_on_the_wall(buyer: User) -> None:
    rfq = services.create_rfq(
        buyer=buyer,
        title="Set up a HK company",
        services_needed=["incorporation"],
        visibility=Visibility.INVITED_ONLY,
    )
    services.publish_rfq(rfq=rfq, buyer=buyer)

    assert list(selectors.open_rfqs()) == []
    assert list(selectors.rfqs_for_buyer(buyer)) == [rfq]


# --------------------------------------------------------------------- quoting


def test_a_member_of_a_claimed_company_may_quote(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    provider, member = make_quoting_provider()

    quote = services.submit_quote(
        rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload
    )

    assert quote.status == QuoteStatus.SUBMITTED
    assert quote.first_year_total.format() == "HKD 1,200.00"
    assert quote.line_items.count() == 3


def test_an_unclaimed_company_may_not_quote(
    open_rfq: Rfq,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    quote_payload: dict[str, Any],
) -> None:
    """A quote is an offer in a licensed company's name to someone who will act
    on it. Proving you are that company is the whole of P3."""
    provider = make_provider()
    member = make_user(email="hopeful@example.com")
    ProviderMember.objects.create(user=member, provider=provider)

    with pytest.raises(services.RfqError):
        services.submit_quote(rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload)


def test_a_stranger_may_not_quote_for_a_company(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    make_user: Callable[..., User],
    quote_payload: dict[str, Any],
) -> None:
    provider, _member = make_quoting_provider()
    stranger = make_user(email="not-a-member@example.com")

    with pytest.raises(services.RfqError):
        services.submit_quote(
            rfq=open_rfq, provider=provider, submitted_by=stranger, **quote_payload
        )


def test_a_company_off_the_register_may_not_quote(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    """Losing the licence stops new offers. The page stays readable - that is
    the registry layer's rule - but the marketplace closes."""
    provider, member = make_quoting_provider()
    with allow_registry_writes():
        provider.licensee.status = LicenceStatus.INACTIVE
        provider.licensee.save(update_fields=["status"])

    with pytest.raises(services.RfqError):
        services.submit_quote(rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload)


def test_nobody_may_quote_on_a_closed_request(
    open_rfq: Rfq,
    buyer: User,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    provider, member = make_quoting_provider()
    services.close_rfq(rfq=open_rfq, buyer=buyer, reason="Went with an accountant friend")

    with pytest.raises(services.RfqError):
        services.submit_quote(rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload)


def test_a_company_cannot_have_two_live_quotes_on_one_request(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    """Two different first-year totals from one company on one screen is not a
    comparison, it is a trap."""
    provider, member = make_quoting_provider()
    services.submit_quote(rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload)

    with pytest.raises(services.RfqError):
        services.submit_quote(rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload)


def test_a_withdrawn_quote_makes_room_for_a_new_one(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    provider, member = make_quoting_provider()
    first = services.submit_quote(
        rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload
    )
    services.withdraw_quote(quote=first, member=member)

    second = services.submit_quote(
        rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload
    )

    assert second.pk != first.pk
    assert selectors.has_quoted(rfq=open_rfq, provider=provider) is True


def test_a_repeated_standard_item_is_refused(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    """The comparison table has one cell per standard item per company; two
    values for one cell has no honest rendering."""
    provider, member = make_quoting_provider()

    with pytest.raises(services.RfqError):
        services.submit_quote(
            rfq=open_rfq,
            provider=provider,
            submitted_by=member,
            first_year_total_minor=1_000_00,
            line_items=[
                {"label": "company_secretary", "amount_minor": 600_00},
                {"label": "company_secretary", "amount_minor": 700_00},
            ],
        )
    assert Quote.objects.count() == 0


def test_a_refused_quote_gives_the_daily_credit_back(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    """The quota is charged inside the same transaction as the write, so a
    submission that fails a later check cannot have cost anything."""
    provider, member = make_quoting_provider()

    with pytest.raises(services.RfqError):
        services.submit_quote(
            rfq=open_rfq,
            provider=provider,
            submitted_by=member,
            first_year_total_minor=1_000_00,
            line_items=[{"label": "not_a_real_item", "amount_minor": 1}],
        )

    assert selectors.quota_state(provider).free_remaining == 3


# ------------------------------------------------------------------- the quota


def test_the_fourth_quote_of_the_day_is_refused_and_a_purchase_unblocks_it(
    buyer: User,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    """ROADMAP's acceptance test for P5, and the free tier's whole definition.

    Four separate requests, because the one-live-quote-per-request rule would
    otherwise be the thing doing the refusing and the quota would go untested.
    """

    def _request(n: int) -> Rfq:
        rfq = services.create_rfq(
            buyer=buyer, title=f"Request {n}", services_needed=["incorporation"]
        )
        return services.publish_rfq(rfq=rfq, buyer=buyer)

    provider, member = make_quoting_provider()
    requests = [_request(n) for n in range(4)]

    for rfq in requests[:3]:
        services.submit_quote(rfq=rfq, provider=provider, submitted_by=member, **quote_payload)

    with pytest.raises(services.QuotaExceeded):
        services.submit_quote(
            rfq=requests[3], provider=provider, submitted_by=member, **quote_payload
        )

    services.grant_quote_credits(provider=provider, credits=2)
    quote = services.submit_quote(
        rfq=requests[3], provider=provider, submitted_by=member, **quote_payload
    )

    assert quote.status == QuoteStatus.SUBMITTED
    ledger = QuotaLedger.objects.get(provider=provider, date=timezone.localdate())
    assert (ledger.free_used, ledger.paid_used, ledger.paid_balance) == (3, 1, 1)


def test_withdrawing_does_not_refund_the_quote(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    """Otherwise "submit, withdraw, submit" is an unlimited allowance - and the
    buyer received every one of those offers anyway."""
    provider, member = make_quoting_provider()
    quote = services.submit_quote(
        rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload
    )

    services.withdraw_quote(quote=quote, member=member)

    assert selectors.quota_state(provider).free_remaining == 2


def test_a_purchased_balance_survives_into_the_next_day(
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    """The free allowance resets daily; bought credit does not expire overnight
    because nobody sold it on that basis."""
    provider, _member = make_quoting_provider()
    yesterday = timezone.localdate() - timedelta(days=1)
    services.grant_quote_credits(provider=provider, credits=5, day=yesterday)

    state = selectors.quota_state(provider)

    assert (state.free_remaining, state.paid_balance) == (3, 5)


def test_reading_the_quota_never_writes_a_row(
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    """Every company that ever opened the wall would otherwise have a ledger
    row for every day it did so."""
    provider, _member = make_quoting_provider()

    assert selectors.quota_state(provider).can_quote is True
    assert QuotaLedger.objects.count() == 0


def test_buying_nothing_is_refused(
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    provider, _member = make_quoting_provider()

    with pytest.raises(services.RfqError):
        services.grant_quote_credits(provider=provider, credits=0)


# --------------------------------------------------------------- buyer choices


def test_accepting_one_quote_declines_the_others_and_closes_the_request(
    open_rfq: Rfq,
    buyer: User,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    first_provider, first_member = make_quoting_provider()
    second_provider, second_member = make_quoting_provider()
    chosen = services.submit_quote(
        rfq=open_rfq, provider=first_provider, submitted_by=first_member, **quote_payload
    )
    other = services.submit_quote(
        rfq=open_rfq, provider=second_provider, submitted_by=second_member, **quote_payload
    )

    services.accept_quote(quote=chosen, buyer=buyer)

    chosen.refresh_from_db()
    other.refresh_from_db()
    open_rfq.refresh_from_db()
    assert chosen.status == QuoteStatus.ACCEPTED
    assert other.status == QuoteStatus.DECLINED
    assert open_rfq.status == RfqStatus.AWARDED


def test_shortlisting_keeps_the_quote_live_and_withdrawing_twice_is_not_an_error(
    open_rfq: Rfq,
    buyer: User,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    """A shortlist is a signal to the buyer, not a decision about the company:
    the offer stays live, and the company may still take it back."""
    provider, member = make_quoting_provider()
    quote = services.submit_quote(
        rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload
    )

    services.shortlist_quote(quote=quote, buyer=buyer)
    assert quote.status == QuoteStatus.SHORTLISTED
    assert quote.is_live is True

    services.withdraw_quote(quote=quote, member=member)
    withdrawn_at = quote.withdrawn_at
    services.withdraw_quote(quote=quote, member=member)

    assert quote.withdrawn_at == withdrawn_at


def test_only_the_buyer_may_shortlist_or_accept(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    make_user: Callable[..., User],
    quote_payload: dict[str, Any],
) -> None:
    provider, member = make_quoting_provider()
    quote = services.submit_quote(
        rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload
    )
    stranger = make_user(email="opportunist@example.com")

    with pytest.raises(services.RfqError):
        services.shortlist_quote(quote=quote, buyer=stranger)
    with pytest.raises(services.RfqError):
        services.accept_quote(quote=quote, buyer=stranger)


def test_an_accepted_quote_cannot_be_withdrawn(
    open_rfq: Rfq,
    buyer: User,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    provider, member = make_quoting_provider()
    quote = services.submit_quote(
        rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload
    )
    services.accept_quote(quote=quote, buyer=buyer)

    with pytest.raises(services.RfqError):
        services.withdraw_quote(quote=quote, member=member)


def test_a_claimed_page_that_lost_its_claim_stops_quoting(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    provider, member = make_quoting_provider()
    provider.claim_status = ClaimStatus.REJECTED
    provider.save(update_fields=["claim_status"])

    with pytest.raises(services.RfqError):
        services.submit_quote(rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload)
