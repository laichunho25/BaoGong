"""Fixtures that apply to the whole suite.

Only one thing lives here, and it is here rather than in an app's conftest
because it is not about any app: the cache is process-global and is not rolled
back the way the database is between tests. It now holds the rate limiters for
sign-in, password reset and verification mail (apps/core/throttling.py), so a
test that exhausts an allowance would otherwise hand the next test a counter
that is already spent - and the failure would depend on test order.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _empty_the_cache() -> None:
    cache.clear()
