"""Fixtures for the site-wide pages.

The home page is assembled out of four apps' selectors, so its tests need the
same factories those apps test with. They are re-exported rather than
rewritten: a second ``make_provider`` here would let the home page keep passing
against a company shape the rest of the codebase no longer has.
"""

from __future__ import annotations

from apps.providers.tests.conftest import (  # noqa: F401  (re-exported fixtures)
    make_licensee,
    make_provider,
    make_upload,
    make_user,
    moderator,
)
from apps.reviews.tests.conftest import make_review  # noqa: F401  (re-exported fixture)
from apps.rfq.tests.conftest import buyer, open_rfq  # noqa: F401  (re-exported fixtures)
