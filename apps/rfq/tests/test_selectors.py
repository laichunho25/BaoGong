"""The reads behind the wall and the comparison table.

The comparison table is the product's central claim - that two quotes can be
put on one basis - so the tests that matter here are about the gaps: what the
table shows when one company priced something the other did not.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from django.utils import timezone

from apps.reviews.models import ReviewStatus
from apps.rfq import selectors, services
from apps.rfq.models import Quote
from apps.rfq.selectors import FIRST_YEAR_TOTAL, MIN_PERCENTILE_SAMPLE

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Review
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


def test_a_buyer_who_left_a_verified_review_is_marked_on_the_wall(
    open_rfq: Rfq,
    make_provider: Callable[..., Provider],
    make_review: Callable[..., Review],
) -> None:
    """The half of the bargain that companies can see. It is an attribute of
    the requirement, not a name: the wall still carries nothing that says who
    the buyer is (COMPLIANCE section 4)."""
    assert selectors.open_rfqs().first().buyer_verified is False  # type: ignore[union-attr]

    make_review(provider=make_provider(), author=open_rfq.buyer, is_verified=True)

    assert selectors.open_rfqs().first().buyer_verified is True  # type: ignore[union-attr]


def test_an_unverified_review_does_not_mark_the_wall(
    open_rfq: Rfq,
    make_provider: Callable[..., Provider],
    make_review: Callable[..., Review],
) -> None:
    """Same rule as the mark itself: the document is what is being rewarded."""
    make_review(provider=make_provider(), author=open_rfq.buyer, is_verified=False)

    assert selectors.open_rfqs().first().buyer_verified is False  # type: ignore[union-attr]


def test_marked_requirements_sort_first_without_hiding_the_others(
    open_rfq: Rfq,
    make_user: Callable[..., User],
    make_provider: Callable[..., Provider],
    make_review: Callable[..., Review],
) -> None:
    """The reward is a soft preference, announced on the page that asks for the
    review. A newer unmarked requirement still appears - below, not gone."""
    newcomer = make_user(email="newcomer@example.com")
    later = services.publish_rfq(
        rfq=services.create_rfq(
            buyer=newcomer,
            title="Incorporate a holding company",
            services_needed=["incorporation"],
            raw_input="Just the company, no account.",
        ),
        buyer=newcomer,
    )
    # Published last, so it leads the wall on recency alone.
    assert list(selectors.open_rfqs()) == [later, open_rfq]

    make_review(provider=make_provider(), author=open_rfq.buyer, is_verified=True)

    assert list(selectors.open_rfqs()) == [open_rfq, later]


def test_hiding_the_review_puts_the_buyer_back_in_line(
    open_rfq: Rfq,
    make_provider: Callable[..., Provider],
    make_review: Callable[..., Review],
) -> None:
    """Nothing about the standing is stored on the requirement, so a review
    hidden after a complaint takes the wall position with it."""
    review = make_review(provider=make_provider(), author=open_rfq.buyer, is_verified=True)

    review.status = ReviewStatus.HIDDEN
    review.save(update_fields=["status"])

    assert selectors.open_rfqs().first().buyer_verified is False  # type: ignore[union-attr]


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


class TestMarketPercentiles:
    """The SQL half of A5.

    AI_AGENTS A5 puts these numbers in Postgres rather than in a prompt, and
    the reason is the thing these tests are about: a percentile is a claim
    about the market, and a model asked for one will produce a number that
    looks exactly like this one and is made up.
    """

    def _quotes(
        self,
        rfq: Rfq,
        make_quoting_provider: Callable[..., tuple[Provider, User]],
        totals: list[int],
        **overrides: Any,
    ) -> list[Quote]:
        quotes = []
        for total in totals:
            provider, member = make_quoting_provider()
            quotes.append(
                services.submit_quote(
                    rfq=rfq,
                    provider=provider,
                    submitted_by=member,
                    first_year_total_minor=total,
                    **overrides,
                )
            )
        return quotes

    def test_a_handful_of_quotes_is_not_a_market(
        self, open_rfq: Rfq, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        """Below the floor nothing is published at all. "Cheaper than four
        other quotes" is not a fact about Hong Kong prices, and the buyer
        cannot tell the difference once it is on the page."""
        self._quotes(open_rfq, make_quoting_provider, [5_000_00] * (MIN_PERCENTILE_SAMPLE - 1))

        assert selectors.market_percentiles() == {}

    def test_enough_quotes_produce_a_first_year_percentile(
        self, open_rfq: Rfq, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        totals = [(4_000 + step * 1_000) * 100 for step in range(MIN_PERCENTILE_SAMPLE)]
        self._quotes(open_rfq, make_quoting_provider, totals)

        market = selectors.market_percentiles()[FIRST_YEAR_TOTAL]

        assert market.sample_size == MIN_PERCENTILE_SAMPLE
        assert market.p10 < market.p50 < market.p90
        assert market.p50 == pytest.approx(7_500_00, abs=100)

    def test_a_quote_is_never_compared_with_itself(
        self, open_rfq: Rfq, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        """With a small sample, leaving the quote in drags the percentile
        towards it and hides the outlier the analysis exists to find."""
        quotes = self._quotes(open_rfq, make_quoting_provider, [8_000_00] * MIN_PERCENTILE_SAMPLE)

        included = selectors.market_percentiles()[FIRST_YEAR_TOTAL]
        excluded = selectors.market_percentiles(exclude_quote=quotes[0])

        assert included.sample_size == MIN_PERCENTILE_SAMPLE
        # One fewer quote takes the sample under the floor, so nothing is
        # published rather than a percentile computed from what is left.
        assert excluded == {}

    def test_a_withdrawn_offer_is_not_a_price_anybody_can_get(
        self, open_rfq: Rfq, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        quotes = self._quotes(open_rfq, make_quoting_provider, [8_000_00] * MIN_PERCENTILE_SAMPLE)
        services.withdraw_quote(quote=quotes[0], member=quotes[0].submitted_by)

        assert selectors.market_percentiles() == {}

    def test_two_currencies_never_land_in_one_percentile(
        self, open_rfq: Rfq, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        self._quotes(open_rfq, make_quoting_provider, [8_000_00] * MIN_PERCENTILE_SAMPLE)
        self._quotes(open_rfq, make_quoting_provider, [5_000_00] * 4, currency="CNY")

        market = selectors.market_percentiles()[FIRST_YEAR_TOTAL]

        assert market.sample_size == MIN_PERCENTILE_SAMPLE

    def test_line_items_get_their_own_percentiles(
        self, open_rfq: Rfq, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        self._quotes(
            open_rfq,
            make_quoting_provider,
            [8_000_00] * MIN_PERCENTILE_SAMPLE,
            line_items=[
                {"label": "company_secretary", "amount_minor": 2_400_00},
                {"label": "other", "amount_minor": 500_00, "custom_label": "Nominee director"},
            ],
        )

        market = selectors.market_percentiles()

        assert market["company_secretary"].p50 == 2_400_00
        # An item one company invented is not a comparison basis.
        assert "other" not in market

    def test_percentiles_come_back_in_whole_minor_units(
        self, open_rfq: Rfq, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        """``PERCENTILE_CONT`` interpolates and answers in fractions of a cent.
        Money on this platform is an integer (CLAUDE.md rule 6)."""
        totals = [(4_000 + step * 111) * 100 + 33 for step in range(MIN_PERCENTILE_SAMPLE)]
        self._quotes(open_rfq, make_quoting_provider, totals)

        market = selectors.market_percentiles()[FIRST_YEAR_TOTAL]

        assert isinstance(market.p50, int)


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


class TestMatchingSnapshot:
    """Counts safe to print on a page anyone can open.

    The home page is public and a requirement is only ever a requirement
    there: who wrote it, and anything that could identify them, stops at the
    login (COMPLIANCE section 4). This selector therefore returns numbers and
    nothing else - the guarantee is that there is no row here to leak.
    """

    def test_it_counts_open_requests_and_recent_quotes(
        self,
        open_rfq: Rfq,
        make_quoting_provider: Callable[..., tuple[Provider, User]],
        quote_payload: dict[str, Any],
    ) -> None:
        provider, member = make_quoting_provider()
        services.submit_quote(rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload)

        snapshot = selectors.matching_snapshot()

        assert snapshot.open_requests == 1
        assert snapshot.quotes_recently == 1

    def test_quotes_outside_the_window_are_not_counted(
        self,
        open_rfq: Rfq,
        make_quoting_provider: Callable[..., tuple[Provider, User]],
        quote_payload: dict[str, Any],
    ) -> None:
        provider, member = make_quoting_provider()
        quote = services.submit_quote(
            rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload
        )
        Quote.objects.filter(pk=quote.pk).update(submitted_at=timezone.now() - timedelta(days=60))

        assert selectors.matching_snapshot(window_days=30).quotes_recently == 0

    def test_it_reports_the_free_allowance_the_page_quotes(self) -> None:
        # The home page prints "每家每日免费 N 次" from this field; a hard-coded
        # number in the template would drift from the setting that enforces it.
        from django.conf import settings

        snapshot = selectors.matching_snapshot()

        assert snapshot.free_quotes_per_day == settings.RFQ_FREE_QUOTES_PER_DAY

    def test_a_draft_requirement_is_not_open(self, buyer: User) -> None:
        services.create_rfq(
            buyer=buyer,
            title="Still thinking about it",
            services_needed=["incorporation"],
            raw_input="Not published yet.",
        )

        assert selectors.matching_snapshot().open_requests == 0


# ------------------------------------------------------------- A2 suggestions


def _stored(*items: dict[str, Any]) -> dict[str, Any]:
    return {"items": list(items), "used_fallback": True}


def test_suggestions_are_read_back_in_the_order_they_were_stored(
    open_rfq: Rfq, make_provider: Callable[..., Provider]
) -> None:
    """The ranking belongs to the suggestion. Re-sorting it in SQL would
    quietly invent a different one."""
    first = make_provider()
    second = make_provider()
    open_rfq.matches = _stored(
        {"provider_id": second.slug, "rank": 1, "reasons": ["a"], "concerns": []},
        {"provider_id": first.slug, "rank": 2, "reasons": [], "concerns": ["b"]},
    )
    open_rfq.save(update_fields=["matches"])

    matches = selectors.suggested_matches(open_rfq)

    assert [match.provider.slug for match in matches] == [second.slug, first.slug]
    assert matches[0].reasons == ["a"]
    assert matches[1].concerns == ["b"]


def test_a_company_struck_off_since_the_suggestion_drops_out(
    open_rfq: Rfq, make_provider: Callable[..., Provider]
) -> None:
    """The suggestion holds slugs, not rows, so a stale copy of a name can
    never be rendered for a company that is no longer on the register."""
    from apps.registry.models import LicenceStatus

    gone = make_provider(licensee_kwargs={"status": LicenceStatus.INACTIVE})
    open_rfq.matches = _stored({"provider_id": gone.slug, "rank": 1})
    open_rfq.save(update_fields=["matches"])

    assert selectors.suggested_matches(open_rfq) == []


def test_a_suggested_company_that_already_quoted_is_marked(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    provider, member = make_quoting_provider()
    services.submit_quote(rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload)
    open_rfq.matches = _stored({"provider_id": provider.slug, "rank": 1})
    open_rfq.save(update_fields=["matches"])

    assert selectors.suggested_matches(open_rfq)[0].has_quoted is True


def test_a_requirement_with_no_suggestion_reads_as_none(open_rfq: Rfq) -> None:
    assert selectors.suggested_matches(open_rfq) == []
