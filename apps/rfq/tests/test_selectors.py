"""The reads behind the wall and the comparison table.

The comparison table is the product's central claim - that two quotes can be
put on one basis - so the tests that matter here are about the gaps: what the
table shows when one company priced something the other did not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from apps.rfq import selectors, services

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.rfq.models import Rfq

pytestmark = pytest.mark.django_db


def test_the_wall_counts_live_quotes_only(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    provider, member = make_quoting_provider()
    quote = services.submit_quote(
        rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload
    )
    assert selectors.open_rfqs().first().quote_count == 1  # type: ignore[union-attr]

    services.withdraw_quote(quote=quote, member=member)

    assert selectors.open_rfqs().first().quote_count == 0  # type: ignore[union-attr]


def test_the_buyer_sees_quotes_cheapest_first(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    dear_provider, dear_member = make_quoting_provider()
    cheap_provider, cheap_member = make_quoting_provider()
    services.submit_quote(
        rfq=open_rfq,
        provider=dear_provider,
        submitted_by=dear_member,
        first_year_total_minor=9_800_00,
    )
    services.submit_quote(
        rfq=open_rfq,
        provider=cheap_provider,
        submitted_by=cheap_member,
        first_year_total_minor=4_200_00,
    )

    totals = [quote.first_year_total_minor for quote in selectors.quotes_for_rfq(open_rfq)]

    assert totals == [4_200_00, 9_800_00]


def test_the_table_shows_a_gap_where_one_company_priced_nothing(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    """ "This one did not price the government fee" is the single most useful
    thing a buyer comparing a cheap quote to an expensive one can be told."""
    full_provider, full_member = make_quoting_provider()
    thin_provider, thin_member = make_quoting_provider()
    services.submit_quote(
        rfq=open_rfq,
        provider=full_provider,
        submitted_by=full_member,
        first_year_total_minor=1_200_00,
        line_items=[
            {"label": "govt_incorporation_fee", "amount_minor": 172_00},
            {"label": "company_secretary", "amount_minor": 600_00},
        ],
    )
    services.submit_quote(
        rfq=open_rfq,
        provider=thin_provider,
        submitted_by=thin_member,
        first_year_total_minor=900_00,
        line_items=[{"label": "company_secretary", "amount_minor": 600_00}],
    )

    quotes = list(selectors.quotes_for_rfq(open_rfq))
    rows = {row.label: row for row in selectors.comparison_rows(quotes)}

    # Both priced the secretary; only one priced the government fee, and the
    # missing one is a cell, not an absent row.
    assert rows["company_secretary"].quoted_by_all is True
    assert rows["govt_incorporation_fee"].quoted_by_all is False
    assert sorted(cell.amount_minor for cell in rows["company_secretary"].cells) == [
        600_00,
        600_00,
    ]


def test_an_item_nobody_priced_is_not_an_empty_row(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    provider, member = make_quoting_provider()
    services.submit_quote(
        rfq=open_rfq,
        provider=provider,
        submitted_by=member,
        first_year_total_minor=600_00,
        line_items=[{"label": "company_secretary", "amount_minor": 600_00}],
    )

    rows = selectors.comparison_rows(list(selectors.quotes_for_rfq(open_rfq)))

    assert [row.label for row in rows] == ["company_secretary"]


def test_missing_standard_items_is_a_set_difference(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    """A5's rule-based half: it has to work with the model switched off."""
    provider, member = make_quoting_provider()
    quote = services.submit_quote(
        rfq=open_rfq,
        provider=provider,
        submitted_by=member,
        first_year_total_minor=600_00,
        line_items=[
            {"label": "company_secretary", "amount_minor": 600_00},
            {"label": "other", "amount_minor": 100_00, "custom_label": "Nominee director"},
        ],
    )

    missing = selectors.missing_standard_items(quote)

    assert "govt_incorporation_fee" in missing
    assert "company_secretary" not in missing
    # 'other' is not a standard item, so it neither fills a gap nor makes one.
    assert "other" not in missing


def test_a_custom_item_shows_the_name_the_company_typed(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    provider, member = make_quoting_provider()
    quote = services.submit_quote(
        rfq=open_rfq,
        provider=provider,
        submitted_by=member,
        first_year_total_minor=100_00,
        line_items=[{"label": "other", "amount_minor": 100_00, "custom_label": "Nominee director"}],
    )

    assert quote.line_items.first().display_label == "Nominee director"  # type: ignore[union-attr]


def test_a_company_sees_its_own_quotes_newest_first(
    buyer: User,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    provider, member = make_quoting_provider()
    for n in range(2):
        rfq = services.create_rfq(
            buyer=buyer, title=f"Request {n}", services_needed=["incorporation"]
        )
        services.submit_quote(
            rfq=services.publish_rfq(rfq=rfq, buyer=buyer),
            provider=provider,
            submitted_by=member,
            **quote_payload,
        )

    quotes = list(selectors.quotes_for_provider(provider))

    assert len(quotes) == 2
    assert quotes[0].submitted_at >= quotes[1].submitted_at
