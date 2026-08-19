"""The quote allowance: how many, on which clock, and for which tier.

PRD section 3.7 sells two different clocks - a claimed free page counts by the
month, a subscription counts by the day - so these tests pin the boundary
between periods as much as the numbers, because a company that could answer
five requests today and five again tomorrow on the free tier would be getting
the paid product for nothing.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest

from apps.providers.models import Tier
from apps.rfq import selectors
from apps.rfq.allowances import DAILY, MONTHLY, Allowance, allowance_for, period_bounds
from apps.rfq.models import QuotaLedger

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider

pytestmark = pytest.mark.django_db

MID_MONTH = date(2026, 3, 15)


def _spent(provider: Provider, day: date, count: int) -> None:
    QuotaLedger.objects.create(provider=provider, date=day, free_used=count)


class TestAllowanceForTier:
    @pytest.mark.parametrize(
        ("tier", "limit", "period"),
        [
            (Tier.FREE, 5, MONTHLY),
            (Tier.VERIFIED, 5, DAILY),
            (Tier.PREMIUM, 20, DAILY),
        ],
    )
    def test_each_tier_gets_the_allowance_the_pricing_page_promises(
        self,
        make_quoting_provider: Callable[..., tuple[Provider, User]],
        tier: str,
        limit: int,
        period: str,
    ) -> None:
        provider, _member = make_quoting_provider(tier=tier)

        allowance = allowance_for(provider)

        assert (allowance.limit, allowance.period) == (limit, period)

    def test_a_suspended_page_answers_on_the_free_allowance(
        self, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        # Paid placement is suspended when a licence leaves the register. If the
        # daily allowance survived that, it would be the one paid benefit the
        # suspension did not reach.
        provider, _member = make_quoting_provider(tier=Tier.PREMIUM)
        provider.paid_placement_suspended_at = date(2026, 3, 1)

        assert allowance_for(provider) == Allowance(5, MONTHLY)


class TestPeriodBounds:
    def test_a_daily_period_is_the_day_itself(self) -> None:
        assert period_bounds(DAILY, MID_MONTH) == (MID_MONTH, MID_MONTH)

    @pytest.mark.parametrize(
        ("day", "last"),
        [
            (date(2026, 2, 3), date(2026, 2, 28)),
            (date(2028, 2, 3), date(2028, 2, 29)),
            (date(2026, 12, 31), date(2026, 12, 31)),
        ],
    )
    def test_a_monthly_period_ends_on_the_real_last_day(self, day: date, last: date) -> None:
        # The month is not 30 days long, and a free tier whose period ended on
        # the 30th would hand out a sixth quote every 31st.
        assert period_bounds(MONTHLY, day) == (day.replace(day=1), last)


class TestWhatIsLeft:
    def test_the_free_tier_counts_everything_spent_this_month(
        self, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        provider, _member = make_quoting_provider(tier=Tier.FREE)
        _spent(provider, date(2026, 3, 2), 3)
        _spent(provider, MID_MONTH, 1)

        assert selectors.quota_state(provider, MID_MONTH).free_remaining == 1

    def test_the_free_tier_starts_the_month_over(
        self, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        provider, _member = make_quoting_provider(tier=Tier.FREE)
        _spent(provider, date(2026, 2, 28), 5)

        state = selectors.quota_state(provider, date(2026, 3, 1))

        assert (state.free_remaining, state.is_monthly) == (5, True)

    def test_a_subscription_starts_the_day_over(
        self, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        provider, _member = make_quoting_provider(tier=Tier.VERIFIED)
        _spent(provider, MID_MONTH, 5)

        assert selectors.quota_state(provider, MID_MONTH).free_remaining == 0
        assert selectors.quota_state(provider, date(2026, 3, 16)).free_remaining == 5

    def test_the_purchased_balance_ignores_the_period_entirely(
        self, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        # Bought credit was not sold with an expiry date, so a new month or a
        # new day must not touch it - only spending it may.
        provider, _member = make_quoting_provider(tier=Tier.FREE)
        QuotaLedger.objects.create(
            provider=provider, date=date(2026, 1, 4), free_used=5, paid_balance=7
        )

        state = selectors.quota_state(provider, MID_MONTH)

        assert (state.free_remaining, state.paid_balance, state.can_quote) == (5, 7, True)
