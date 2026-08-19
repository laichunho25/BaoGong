"""How many requests a company may answer, and over what clock.

PRD section 3.7 prices the allowance per tier, and deliberately on two
different clocks. A claimed but unpaid page gets five answers a *month*: enough
that claiming is worth doing and that the wall gets answered, not enough to run
a sales desk on. A subscription buys a *daily* allowance instead, which is the
difference a company actually feels when it decides whether to pay.

The rule is a pure function of the tier and the date so that the wall, the
quote form and the service that spends the quota cannot drift apart on what a
company has left - one of them writes, two of them only render, and all three
ask here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from django.conf import settings

from apps.providers.models import Provider, Tier

DAILY: Final = "day"
MONTHLY: Final = "month"


@dataclass(frozen=True, slots=True)
class Allowance:
    """A number of free quotes and the period it resets over."""

    limit: int
    period: str

    @property
    def is_monthly(self) -> bool:
        return self.period == MONTHLY


def allowance_for(provider: Provider) -> Allowance:
    """The company's free allowance under the tier it is actually treated as.

    ``effective_tier``, not ``tier``: a page whose paid placement is suspended
    ranks as free everywhere else, and an allowance that survived the
    suspension would be the one paid benefit a suspension did not touch.
    """
    tier = provider.effective_tier
    if tier == Tier.PREMIUM:
        return Allowance(int(settings.RFQ_QUOTES_PER_DAY_PREMIUM), DAILY)
    if tier == Tier.VERIFIED:
        return Allowance(int(settings.RFQ_QUOTES_PER_DAY_VERIFIED), DAILY)
    return Allowance(int(settings.RFQ_FREE_QUOTES_PER_MONTH), MONTHLY)


def period_bounds(period: str, day: date) -> tuple[date, date]:
    """The inclusive range of ledger rows that count against one allowance."""
    if period == MONTHLY:
        start = day.replace(day=1)
        return start, (start + timedelta(days=31)).replace(day=1) - timedelta(days=1)
    return day, day
