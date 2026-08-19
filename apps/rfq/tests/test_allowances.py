"""The quote allowance: how many, on which clock, and for which tier.

PRD section 3.7 currently counts every tier by the month, because on a young
wall a daily number is one no company can reach. The daily clock stays
supported, so these tests pin both: the numbers we ship with, and that
switching a tier to ``/day`` moves the period boundary rather than only the
number.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

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


class TestParsingTheSpec:
    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("3/month", Allowance(3, MONTHLY)),
            ("15 / months", Allowance(15, MONTHLY)),
            ("5/day", Allowance(5, DAILY)),
            ("0/month", Allowance(0, MONTHLY)),
        ],
    )
    def test_it_reads_the_number_and_the_clock_together(
        self, spec: str, expected: Allowance
    ) -> None:
        assert Allowance.parse(spec) == expected

    @pytest.mark.parametrize("spec", ["3", "3/week", "many/month", "-1/month", ""])
    def test_it_refuses_anything_it_cannot_read(self, spec: str) -> None:
        # A mistyped allowance that fell back to a default would be a pricing
        # change nobody made, found by the first company to run out.
        with pytest.raises(ImproperlyConfigured):
            Allowance.parse(spec)


class TestAllowanceForTier:
    @pytest.mark.parametrize(
        ("tier", "expected"),
        [
            (Tier.FREE, Allowance(3, MONTHLY)),
            (Tier.VERIFIED, Allowance(15, MONTHLY)),
            (Tier.PREMIUM, Allowance(40, MONTHLY)),
        ],
    )
    def test_each_tier_gets_the_allowance_the_pricing_page_promises(
        self,
        make_quoting_provider: Callable[..., tuple[Provider, User]],
        tier: str,
        expected: Allowance,
    ) -> None:
        provider, _member = make_quoting_provider(tier=tier)

        assert allowance_for(provider) == expected

    def test_a_suspended_page_answers_on_the_free_allowance(
        self, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        # Paid placement is suspended when a licence leaves the register. If the
        # paid allowance survived that, it would be the one paid benefit the
        # suspension did not reach.
        provider, _member = make_quoting_provider(tier=Tier.PREMIUM)
        provider.paid_placement_suspended_at = date(2026, 3, 1)

        assert allowance_for(provider) == Allowance(3, MONTHLY)


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
        # The month is not 30 days long, and a tier whose period ended on the
        # 30th would hand out an extra quote every 31st.
        assert period_bounds(MONTHLY, day) == (day.replace(day=1), last)


class TestWhatIsLeft:
    def test_it_counts_everything_spent_this_month(
        self, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        provider, _member = make_quoting_provider(tier=Tier.FREE)
        _spent(provider, date(2026, 3, 2), 2)
        _spent(provider, MID_MONTH, 1)

        assert selectors.quota_state(provider, MID_MONTH).free_remaining == 0

    def test_the_month_starts_over(
        self, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        provider, _member = make_quoting_provider(tier=Tier.FREE)
        _spent(provider, date(2026, 2, 28), 3)

        state = selectors.quota_state(provider, date(2026, 3, 1))

        assert (state.free_remaining, state.is_monthly) == (3, True)

    @override_settings(RFQ_QUOTA_VERIFIED="5/day")
    def test_a_tier_moved_to_the_daily_clock_starts_the_day_over(
        self, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        # The switch we will make once the wall carries enough requests for a
        # daily number to mean anything. It must move the boundary, not just
        # the number.
        provider, _member = make_quoting_provider(tier=Tier.VERIFIED)
        _spent(provider, MID_MONTH, 5)

        assert selectors.quota_state(provider, MID_MONTH).free_remaining == 0
        assert selectors.quota_state(provider, date(2026, 3, 16)).free_remaining == 5

    def test_the_purchased_balance_ignores_the_period_entirely(
        self, make_quoting_provider: Callable[..., tuple[Provider, User]]
    ) -> None:
        # Bought credit was not sold with an expiry date, so a new month must
        # not touch it - only spending it may.
        provider, _member = make_quoting_provider(tier=Tier.FREE)
        QuotaLedger.objects.create(
            provider=provider, date=date(2026, 1, 4), free_used=3, paid_balance=7
        )

        state = selectors.quota_state(provider, MID_MONTH)

        assert (state.free_remaining, state.paid_balance, state.can_quote) == (3, 7, True)
