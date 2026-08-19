"""The one thing the platform gives back for an NNC1.

The invitation on the home page is an offer, and these tests are what keep it
honest: the standing has to follow the evidence - and disappear with it - and
it has to be indifferent to what the review said. A reward that quietly
favoured five-star reviews would be worth more to us and would make every score
on the platform worth less (COMPLIANCE section 3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from apps.reviews.models import ReviewStatus
from apps.reviews.selectors import is_verified_reviewer

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Review

pytestmark = pytest.mark.django_db


def test_someone_who_never_wrote_a_review_has_no_standing(
    make_user: Callable[..., User],
) -> None:
    assert is_verified_reviewer(make_user(email="quiet@example.com")) is False


def test_a_signed_out_visitor_has_no_standing() -> None:
    """The home page calls this for every visitor, most of whom are anonymous."""
    from django.contrib.auth.models import AnonymousUser

    assert is_verified_reviewer(AnonymousUser()) is False
    assert is_verified_reviewer(None) is False


def test_a_published_verified_review_earns_it(
    make_review: Callable[..., Review],
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
) -> None:
    author = make_user(email="shareholder@example.com")
    make_review(provider=make_provider(), author=author, is_verified=True)

    assert is_verified_reviewer(author) is True


def test_a_review_that_was_never_checked_against_a_document_does_not(
    make_review: Callable[..., Review],
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
) -> None:
    """The document is the whole point. An unverified review is welcome on the
    site and earns nothing here."""
    author = make_user(email="unverified@example.com")
    make_review(provider=make_provider(), author=author, is_verified=False)

    assert is_verified_reviewer(author) is False


def test_a_review_still_waiting_for_moderation_does_not(
    make_review: Callable[..., Review],
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
) -> None:
    author = make_user(email="pending@example.com")
    make_review(
        provider=make_provider(),
        author=author,
        status=ReviewStatus.PENDING_MODERATION,
        is_verified=True,
    )

    assert is_verified_reviewer(author) is False


def test_hiding_the_review_takes_the_standing_with_it(
    make_review: Callable[..., Review],
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
) -> None:
    """Derived rather than stored: a review hidden after a complaint stops
    conferring standing the moment it stops being readable."""
    author = make_user(email="hidden@example.com")
    review = make_review(provider=make_provider(), author=author, is_verified=True)

    review.status = ReviewStatus.HIDDEN
    review.save(update_fields=["status"])

    assert is_verified_reviewer(author) is False


@pytest.mark.parametrize("overall", ["1.0", "5.0"])
def test_one_star_earns_exactly_what_five_stars_earns(
    make_review: Callable[..., Review],
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    overall: str,
) -> None:
    """COMPLIANCE section 3. If this test ever needs changing, the promise
    printed on the home page needs changing first."""
    author = make_user(email=f"score{overall}@example.com")
    make_review(provider=make_provider(), author=author, overall=overall, is_verified=True)

    assert is_verified_reviewer(author) is True


def test_standing_belongs_to_the_author_and_nobody_else(
    make_review: Callable[..., Review],
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
) -> None:
    author = make_user(email="author@example.com")
    bystander = make_user(email="bystander@example.com")
    make_review(provider=make_provider(), author=author, is_verified=True)

    assert is_verified_reviewer(bystander) is False
