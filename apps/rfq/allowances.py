"""How many requests a company may answer, and over what clock.

PRD section 3.7 prices the allowance per tier. Every tier currently counts by
the *month*, because on a wall that carries tens of requests a month a daily
number is one no company can reach, and an allowance nobody can hit sells
nothing. The daily clock stays implemented and configurable so that switching
to it - when the wall is busy enough for "five a day" to mean something - is an
environment change and not a release.

Hence the spec strings: ``"3/month"`` says the number and the clock together,
which is exactly how the pricing page has to say it too. The rule is a pure
function of the tier so that the wall, the quote form and the service that
spends the quota cannot drift apart on what a company has left - one of them
writes, two of them only render, and all three ask here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.providers.models import Provider, Tier

DAILY: Final = "day"
MONTHLY: Final = "month"
PERIODS: Final = (DAILY, MONTHLY)


@dataclass(frozen=True, slots=True)
class Allowance:
    """A number of free quotes and the period it resets over."""

    limit: int
    period: str

    @property
    def is_monthly(self) -> bool:
        return self.period == MONTHLY

    @classmethod
    def parse(cls, spec: str) -> Allowance:
        """Read ``"15/month"``. Refuses anything else, loudly and at startup.

        A mistyped allowance that quietly fell back to a default would be a
        pricing change nobody made, discovered by the first company to run out.
        """
        raw_limit, _, raw_period = str(spec).partition("/")
        period = raw_period.strip().lower().rstrip("s")
        try:
            limit = int(raw_limit.strip())
        except ValueError as exc:
            raise ImproperlyConfigured(f"Quota {spec!r} does not start with a number.") from exc
        if limit < 0:
            raise ImproperlyConfigured(f"Quota {spec!r} is negative.")
        if period not in PERIODS:
            raise ImproperlyConfigured(f"Quota {spec!r} must end in /day or /month.")
        return cls(limit, period)


def allowance_for(provider: Provider) -> Allowance:
    """The company's free allowance under the tier it is actually treated as.

    ``effective_tier``, not ``tier``: a page whose paid placement is suspended
    ranks as free everywhere else, and an allowance that survived the
    suspension would be the one paid benefit a suspension did not touch.
    """
    tier = provider.effective_tier
    if tier == Tier.PREMIUM:
        return Allowance.parse(settings.RFQ_QUOTA_PREMIUM)
    if tier == Tier.VERIFIED:
        return Allowance.parse(settings.RFQ_QUOTA_VERIFIED)
    return Allowance.parse(settings.RFQ_QUOTA_FREE)


def period_bounds(period: str, day: date) -> tuple[date, date]:
    """The inclusive range of ledger rows that count against one allowance."""
    if period == MONTHLY:
        start = day.replace(day=1)
        return start, (start + timedelta(days=31)).replace(day=1) - timedelta(days=1)
    return day, day
