"""Submission, moderation and the right of reply.

Written around the promises each flow makes: to the buyer (their review is not
public until a human looked at it, and cannot be quietly rewritten), to the
company (nobody can review themselves, and they get an answer), and to whoever
has to defend a published statement months later (a moderator's name and a
reason are attached to every decision).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from apps.accounts.models import ProviderMember
from apps.reviews import selectors, services
from apps.reviews.models import Review, ReviewStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider

pytestmark = pytest.mark.django_db

SCORES: dict[str, Any] = {
    "price_transparency": Decimal("4.5"),
    "responsiveness": Decimal("4.5"),
    "bank_support": Decimal("4.5"),
    "professionalism": Decimal("4.5"),
    "after_sales": Decimal("4.5"),
}


def _submit(provider: Provider, author: User, **overrides: Any) -> Review:
    kwargs: dict[str, Any] = {
        "provider": provider,
        "author": author,
        "body": "  They filed the incorporation in four days and explained every fee.  ",
        "scores": SCORES,
    }
    kwargs.update(overrides)
    return services.submit_review(**kwargs)


def test_a_new_review_is_not_public(
    make_provider: Callable[..., Provider], make_user: Callable[..., User]
) -> None:
    """The default is the closed one: a defamation claim is answered by
    "it was never public", not by how fast it came down."""
    review = _submit(make_provider(), make_user())

    assert review.status == ReviewStatus.PENDING_MODERATION
    assert review.is_public is False
    assert review.published_at is None
    assert review.is_verified is False
    assert review.body.startswith("They filed")


def test_submission_stores_the_five_dimensions_and_derives_the_overall(
    make_provider: Callable[..., Provider], make_user: Callable[..., User]
) -> None:
    review = _submit(
        make_provider(),
        make_user(),
        scores={**SCORES, "bank_support": None, "after_sales": Decimal("3.5")},
    )

    assert review.score.bank_support is None
    assert review.score.price_transparency == Decimal("4.5")
    assert review.overall == Decimal("4.3")  # (4.5+4.5+4.5+3.5)/4 = 4.25 -> 4.3


def test_a_company_cannot_review_itself(
    make_provider: Callable[..., Provider], make_user: Callable[..., User]
) -> None:
    """The one form of astroturfing the platform can detect for certain."""
    provider = make_provider()
    member = make_user()
    ProviderMember.objects.create(user=member, provider=provider)

    with pytest.raises(services.ReviewError):
        _submit(provider, member)

    assert Review.objects.count() == 0


def test_one_account_gets_one_review_per_company(
    make_provider: Callable[..., Provider], make_user: Callable[..., User]
) -> None:
    provider, author = make_provider(), make_user()
    _submit(provider, author)

    with pytest.raises(services.ReviewError):
        _submit(provider, author)

    assert Review.objects.filter(provider=provider, author=author).count() == 1


def test_publishing_needs_a_moderator(
    make_provider: Callable[..., Provider], make_user: Callable[..., User]
) -> None:
    review = _submit(make_provider(), make_user())

    with pytest.raises(services.ReviewError):
        services.publish_review(review=review, moderator=make_user(), note="looks fine")

    review.refresh_from_db()
    assert review.status == ReviewStatus.PENDING_MODERATION


def test_a_decision_without_a_reason_is_refused(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    moderator: User,
) -> None:
    """Somebody has to be able to answer "on what grounds" months later."""
    review = _submit(make_provider(), make_user())

    with pytest.raises(services.ReviewError):
        services.publish_review(review=review, moderator=moderator, note="   ")


def test_publishing_records_who_decided_and_why(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    moderator: User,
) -> None:
    review = _submit(make_provider(), make_user())

    services.publish_review(review=review, moderator=moderator, note="Reads as first-hand.")
    review.refresh_from_db()

    assert review.status == ReviewStatus.PUBLISHED
    assert review.published_at is not None
    assert review.moderated_by == moderator
    assert review.moderation_note == "Reads as first-hand."


def test_hiding_keeps_the_text(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    moderator: User,
) -> None:
    """COMPLIANCE section 3: hidden, not deleted - the dispute trail keeps its
    subject and the author keeps their words."""
    review = _submit(make_provider(), make_user())
    services.publish_review(review=review, moderator=moderator, note="ok")

    services.hide_review(review=review, moderator=moderator, note="Company disputes the facts.")
    review.refresh_from_db()

    assert review.status == ReviewStatus.HIDDEN
    assert review.body
    assert review.published_at is not None  # it was public once; that stays true


def test_removing_is_available_for_content_that_must_not_exist(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    moderator: User,
) -> None:
    review = _submit(make_provider(), make_user())

    services.remove_review(review=review, moderator=moderator, note="Contains a home address.")
    review.refresh_from_db()

    assert review.status == ReviewStatus.REMOVED
    assert review not in selectors.published_reviews(review.provider)


def test_a_decision_refreshes_the_company_score(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    moderator: User,
) -> None:
    """The score must never lag behind what a visitor can read on the page."""
    provider = make_provider()
    review = _submit(provider, make_user())
    review.is_verified = True  # as NNC1 verification would leave it (P4-2)
    review.save(update_fields=["is_verified"])

    services.publish_review(review=review, moderator=moderator, note="Verified engagement.")
    provider.refresh_from_db()

    assert provider.rating_cached == Decimal("4.95")

    services.hide_review(review=review, moderator=moderator, note="Withdrawn by the author.")
    provider.refresh_from_db()

    assert provider.rating_cached is None
    assert provider.rating_count == 0


def test_only_a_member_can_reply(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    moderator: User,
) -> None:
    provider = make_provider()
    review = _submit(provider, make_user())
    services.publish_review(review=review, moderator=moderator, note="ok")

    with pytest.raises(services.ReviewError):
        services.reply_to_review(review=review, author=make_user(), body="Not our client.")


def test_a_reply_is_public_immediately(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    moderator: User,
) -> None:
    """COMPLIANCE section 3: making the answer wait in the same queue as the
    accusation would make the right of reply worth less than the accusation."""
    provider = make_provider()
    member = make_user()
    ProviderMember.objects.create(user=member, provider=provider)
    review = _submit(provider, make_user())
    services.publish_review(review=review, moderator=moderator, note="ok")

    reply = services.reply_to_review(review=review, author=member, body="We refunded in full.")

    assert reply.is_public is True
    assert reply.provider == provider

    with pytest.raises(services.ReviewError):
        services.reply_to_review(review=review, author=member, body="And again.")


def test_a_company_cannot_reply_to_a_review_nobody_can_read(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
) -> None:
    provider = make_provider()
    member = make_user()
    ProviderMember.objects.create(user=member, provider=provider)
    review = _submit(provider, make_user())

    with pytest.raises(services.ReviewError):
        services.reply_to_review(review=review, author=member, body="Premature.")


def test_selectors_only_ever_return_published_reviews(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    """The whole safety property of the read layer, in one assertion."""
    provider = make_provider()
    published = make_review(provider=provider, author=make_user())
    for status in (ReviewStatus.PENDING_MODERATION, ReviewStatus.HIDDEN, ReviewStatus.REMOVED):
        make_review(provider=provider, author=make_user(), status=status)

    assert list(selectors.published_reviews(provider)) == [published]
    assert selectors.moderation_queue().count() == 1


def test_an_author_can_see_their_own_unpublished_review(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
) -> None:
    """Otherwise a buyer who submits one has no way to know it exists."""
    provider, author = make_provider(), make_user()
    review = _submit(provider, author)

    assert selectors.review_by_author(provider=provider, author=author) == review
    assert list(selectors.reviews_by_author(author)) == [review]
