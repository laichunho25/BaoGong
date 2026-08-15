"""The score itself: RATING_SYSTEM sections 1-4.

This is the number the whole platform is judged on, so the tests here are
written against the published rules rather than against the implementation. The
two the roadmap names explicitly - "one verified 4.5 review shows 4.95" and
"no reviews shows no score at all" - are the first two cases below.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from apps.reviews import services
from apps.reviews.models import ReviewStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Review

pytestmark = pytest.mark.django_db


def test_one_verified_review_lands_on_the_documented_number(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    """(10*5 + 4.5) / (10 + 1) = 4.954... -> 4.95 (RATING_SYSTEM section 1)."""
    provider = make_provider()
    make_review(provider=provider, author=make_user(), overall="4.5")

    services.recompute_provider_rating(str(provider.pk))
    provider.refresh_from_db()

    assert provider.rating_cached == Decimal("4.95")
    assert provider.rating_count == 1
    assert provider.verified_review_count == 1


def test_a_company_with_no_reviews_has_no_score(
    make_provider: Callable[..., Provider],
) -> None:
    """Null, not the prior's 5.00 (RATING_SYSTEM section 4).

    The prior exists to damp small samples. Showing it to a visitor would hand
    a perfect score to every company nobody has ever reviewed - the exact claim
    a comparison site cannot afford to make.
    """
    provider = make_provider()

    services.recompute_provider_rating(str(provider.pk))
    provider.refresh_from_db()

    assert provider.rating_cached is None
    assert provider.has_verified_reviews is False


def test_unverified_reviews_are_counted_but_carry_no_weight(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    """RATING_SYSTEM section 2 v1: weight 1.0 verified, 0.0 unverified."""
    provider = make_provider()
    make_review(provider=provider, author=make_user(), overall="1.0", is_verified=False)

    services.recompute_provider_rating(str(provider.pk))
    provider.refresh_from_db()

    assert provider.rating_count == 1
    assert provider.verified_review_count == 0
    # One furious unverified review must not be able to move the score, or the
    # NNC1 requirement buys nothing.
    assert provider.rating_cached is None


def test_unpublished_reviews_are_invisible_to_the_score(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    provider = make_provider()
    for status in (ReviewStatus.PENDING_MODERATION, ReviewStatus.HIDDEN, ReviewStatus.REMOVED):
        make_review(provider=provider, author=make_user(), overall="1.0", status=status)

    services.recompute_provider_rating(str(provider.pk))
    provider.refresh_from_db()

    assert provider.rating_count == 0
    assert provider.rating_cached is None


def test_the_prior_stops_a_single_bad_review_reading_as_one_star(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    """(10*5 + 1.0) / 11 = 4.64 - the point of the prior."""
    provider = make_provider()
    make_review(provider=provider, author=make_user(), overall="1.0")

    services.recompute_provider_rating(str(provider.pk))
    provider.refresh_from_db()

    assert provider.rating_cached == Decimal("4.64")


def test_recompute_reads_the_reviews_rather_than_adjusting_a_total(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    """A wrong cached number must be repairable by running it again."""
    provider = make_provider()
    make_review(provider=provider, author=make_user(), overall="4.5")
    provider.rating_cached = Decimal("1.00")
    provider.rating_count = 99
    provider.save(update_fields=["rating_cached", "rating_count"])

    services.recompute_provider_rating(str(provider.pk))
    provider.refresh_from_db()

    assert provider.rating_cached == Decimal("4.95")
    assert provider.rating_count == 1


def test_recompute_survives_a_provider_that_no_longer_exists(
    make_provider: Callable[..., Provider],
) -> None:
    """The Celery task can arrive after a delete; it must not crash the worker."""
    provider = make_provider()
    provider_id = str(provider.pk)
    provider.delete()

    assert services.recompute_provider_rating(provider_id) is None


def test_score_overall_ignores_a_service_the_buyer_never_bought(
    make_provider: Callable[..., Provider],
) -> None:
    """RATING_SYSTEM section 3: null bank_support drops out of the mean."""
    scores = {
        "price_transparency": Decimal("5.0"),
        "responsiveness": Decimal("5.0"),
        "bank_support": None,
        "professionalism": Decimal("4.0"),
        "after_sales": Decimal("4.0"),
    }

    # Mean of four, not five with a zero - which would read 3.6.
    assert services.score_overall(scores) == Decimal("4.5")


def test_score_overall_refuses_an_empty_review() -> None:
    with pytest.raises(services.ReviewError):
        services.score_overall(dict.fromkeys(("price_transparency", "responsiveness")))


def test_dimension_ratings_leave_an_unrated_dimension_empty(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    provider = make_provider()
    make_review(
        provider=provider,
        author=make_user(),
        overall="4.0",
        scores={"bank_support": None},
    )

    ratings = services.dimension_ratings(provider)

    assert ratings["bank_support"] is None
    assert ratings["responsiveness"] == Decimal("4.91")


def test_dimension_ratings_only_look_at_verified_reviews(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    """The chart and the headline number must be made of the same evidence."""
    provider = make_provider()
    make_review(provider=provider, author=make_user(), overall="1.0", is_verified=False)

    assert all(value is None for value in services.dimension_ratings(provider).values())
