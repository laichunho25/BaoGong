"""Fixtures for the review layer.

The company and account factories are the provider layer's, re-exported rather
than rewritten: a second ``make_provider`` that drifted from the first would
make review tests pass against a company shape that no longer exists.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from apps.providers.tests.conftest import (  # noqa: F401  (re-exported fixtures)
    make_licensee,
    make_provider,
    make_upload,
    make_user,
    moderator,
)
from apps.reviews.models import SCORE_FIELDS, Review, ReviewScore, ReviewStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider


@pytest.fixture
def make_review() -> Callable[..., Review]:
    """A review in whatever state the test needs, written straight to the DB.

    Deliberately not routed through ``submit_review``: most tests are about what
    happens to a review that is *already* published or verified, and getting
    there through the real flow would need a moderator in every one of them.
    """

    def _make(
        *,
        provider: Provider,
        author: User,
        overall: str = "4.5",
        scores: dict[str, Any] | None = None,
        status: str = ReviewStatus.PUBLISHED,
        is_verified: bool = True,
        **overrides: Any,
    ) -> Review:
        fields: dict[str, Any] = {
            "body": "They handled the incorporation and the bank introduction.",
        }
        fields.update(overrides)
        review = Review.objects.create(
            provider=provider,
            author=author,
            overall=Decimal(overall),
            status=status,
            is_verified=is_verified,
            **fields,
        )
        values = {field: Decimal(overall) for field in SCORE_FIELDS}
        values.update(scores or {})
        ReviewScore.objects.create(review=review, **values)
        return review

    return _make
