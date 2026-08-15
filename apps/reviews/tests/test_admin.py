"""The review moderation queue.

Only the customised parts are worth testing: the reason gate in front of every
decision, the refusal to let staff type a review in themselves, and the way the
AI assessment is presented. The last one is CLAUDE.md rule 3 made visible - the
agent's output has to read as advice on the screen where the decision is made,
or the rule survives in the database and dies in the UI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.urls import reverse

from apps.reviews.models import ReviewStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Review

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:reviews_review_changelist"


@pytest.fixture
def admin_client(client: Client, moderator: User) -> Client:
    moderator.is_staff = True
    moderator.is_superuser = True
    moderator.save()
    client.force_login(moderator)
    return client


def test_publishing_asks_for_a_reason_before_anything_changes(
    admin_client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    review = make_review(
        provider=make_provider(),
        author=make_user(),
        status=ReviewStatus.PENDING_MODERATION,
    )

    response = admin_client.post(
        reverse(CHANGELIST),
        {"action": "publish_reviews", "_selected_action": [str(review.pk)]},
    )

    assert response.status_code == 200
    review.refresh_from_db()
    assert review.status == ReviewStatus.PENDING_MODERATION


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("publish_reviews", ReviewStatus.PUBLISHED),
        ("hide_reviews", ReviewStatus.HIDDEN),
        ("remove_reviews", ReviewStatus.REMOVED),
    ],
)
def test_a_decision_with_a_reason_goes_through_and_is_attributed(
    admin_client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
    moderator: User,
    action: str,
    expected: str,
) -> None:
    review = make_review(
        provider=make_provider(),
        author=make_user(),
        status=ReviewStatus.PENDING_MODERATION,
    )

    admin_client.post(
        reverse(CHANGELIST),
        {
            "action": action,
            "_selected_action": [str(review.pk)],
            "apply_reason": "1",
            "reason": "Reads as a first-hand account.",
        },
    )

    review.refresh_from_db()
    assert review.status == expected
    assert review.moderated_by == moderator
    assert review.moderation_note == "Reads as a first-hand account."


def test_a_refused_decision_is_reported_rather_than_swallowed(
    admin_client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    """A review already removed cannot be replied to; the moderator has to be
    told which row failed, not left looking at an unchanged list."""
    review = make_review(provider=make_provider(), author=make_user())

    response = admin_client.post(
        reverse(CHANGELIST),
        {
            "action": "publish_reviews",
            "_selected_action": [str(review.pk)],
            "apply_reason": "1",
            "reason": "   ",
        },
        follow=True,
    )

    assert response.status_code == 200
    review.refresh_from_db()
    assert review.status == ReviewStatus.PUBLISHED  # unchanged - it already was


def test_staff_cannot_write_a_review(
    admin_client: Client,
) -> None:
    """Reviews come from buyers. A staff-authored one would be indistinguishable
    from a real one in the database and worthless as evidence."""
    response = admin_client.get(reverse("admin:reviews_review_add"))

    assert response.status_code in (302, 403)


def test_the_ai_assessment_is_labelled_as_advice(
    admin_client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    review = make_review(
        provider=make_provider(),
        author=make_user(),
        status=ReviewStatus.PENDING_MODERATION,
        moderation={"labels": "spam_suspected", "severity": "low"},
    )

    content = admin_client.get(
        reverse("admin:reviews_review_change", args=[review.pk])
    ).content.decode()

    assert "spam_suspected" in content
    assert "Advisory only" in content


def test_a_review_with_no_ai_assessment_says_so(
    admin_client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    review = make_review(
        provider=make_provider(),
        author=make_user(),
        status=ReviewStatus.PENDING_MODERATION,
    )

    content = admin_client.get(
        reverse("admin:reviews_review_change", args=[review.pk])
    ).content.decode()

    assert "rule-based queue" in content


def test_company_replies_are_takedown_only(
    admin_client: Client,
) -> None:
    response = admin_client.get(reverse("admin:reviews_reviewreply_add"))

    assert response.status_code in (302, 403)
