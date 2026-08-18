"""The reads the home page's review section is built on.

The landing page is where the platform states its rule about reviews - that a
review counts once a moderator has confirmed the NNC1. So the section that
shows reviews there has to show only the ones that rule covers; anything else
would be the page advertising a standard it breaks on itself.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone

from apps.reviews import selectors
from apps.reviews.models import ReviewStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Review

pytestmark = pytest.mark.django_db


def test_only_published_and_verified_reviews_are_featured(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    provider = make_provider()
    featured = make_review(provider=provider, author=make_user(email="a@example.com"))
    make_review(
        provider=make_provider(),
        author=make_user(email="b@example.com"),
        is_verified=False,
    )
    make_review(
        provider=make_provider(),
        author=make_user(email="c@example.com"),
        status=ReviewStatus.PENDING_MODERATION,
    )
    make_review(
        provider=make_provider(),
        author=make_user(email="d@example.com"),
        status=ReviewStatus.HIDDEN,
    )

    assert list(selectors.featured_reviews()) == [featured]


def test_the_order_is_time_not_score(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    # Ordering by score would quietly turn the section into a place where only
    # flattering reviews live, which is exactly what the page claims it is not.
    now = timezone.now()
    older = make_review(
        provider=make_provider(),
        author=make_user(email="old@example.com"),
        overall="5.0",
        published_at=now - timedelta(days=7),
    )
    newer = make_review(
        provider=make_provider(),
        author=make_user(email="new@example.com"),
        overall="3.0",
        published_at=now,
    )

    assert list(selectors.featured_reviews()) == [newer, older]


def test_it_respects_the_limit(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    for index in range(4):
        make_review(
            provider=make_provider(),
            author=make_user(email=f"author{index}@example.com"),
            published_at=timezone.now() - timedelta(hours=index),
        )

    assert len(selectors.featured_reviews(limit=2)) == 2
    assert len(selectors.featured_reviews()) == 3
