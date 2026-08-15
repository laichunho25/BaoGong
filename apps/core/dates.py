"""Deadlines expressed in working days.

COMPLIANCE section 3 promises a dispute is handled within five working days,
so the platform needs one definition of "working day" rather than one per
caller. This is the whole of it: weekends do not count, everything else does.

Hong Kong public holidays are deliberately **not** modelled. Doing it properly
means a maintained calendar (the general holidays are gazetted yearly, and some
move with the lunar calendar), and getting it wrong in the generous direction
would quietly extend a promise the platform made to users. A deadline that is
one day tight on Chinese New Year is the safe side of that error, and the
moderator queue is staffed by people who can see the date.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

#: Monday is 0. Saturday and Sunday do not count towards a deadline.
WEEKEND = (5, 6)


def business_days_from(start: datetime, days: int) -> datetime:
    """``start`` plus ``days`` working days, keeping the time of day.

    Counting forward one day at a time rather than doing arithmetic on weeks:
    the numbers here are single digits, and the loop is the version a person can
    check against a calendar.
    """
    if days < 0:
        raise ValueError("days must not be negative")
    moment = start
    remaining = days
    while remaining > 0:
        moment += timedelta(days=1)
        if moment.weekday() not in WEEKEND:
            remaining -= 1
    return moment
