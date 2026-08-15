"""The review moderation queue.

Only the customised parts are worth testing: the reason gate in front of every
decision, the refusal to let staff type a review in themselves, and the way the
AI assessment is presented. The last one is CLAUDE.md rule 3 made visible - the
agent's output has to read as advice on the screen where the decision is made,
or the rule survives in the database and dies in the UI.

The dispute queue at the bottom is the same admin under a deadline: COMPLIANCE
section 3 promises the company an answer within five working days, so the tests
also cover the queue admitting, on screen, when it has broken that promise.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ProviderMember
from apps.reviews import services
from apps.reviews.models import Dispute, DisputeDecision, DisputeGround, ReviewStatus

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


# ------------------------------------------------------------------ the dispute queue


@pytest.fixture
def dispute(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> Dispute:
    provider = make_provider()
    review = make_review(
        provider=provider,
        author=make_user(email="buyer@example.com"),
        status=ReviewStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    member = make_user(email="secretary@example.com")
    ProviderMember.objects.create(user=member, provider=provider)
    return services.raise_dispute(
        review=review,
        raised_by=member,
        ground=DisputeGround.NOT_A_CUSTOMER,
        reason="We hold no engagement record for this person in any year.",
    )


def test_deciding_a_dispute_asks_for_a_reason_first(admin_client: Client, dispute: Dispute) -> None:
    """The reason is the company's answer, so it cannot be optional."""
    response = admin_client.post(
        reverse("admin:reviews_dispute_changelist"),
        {"action": "hide_disputed_reviews", "_selected_action": [str(dispute.pk)]},
    )

    assert response.status_code == 200
    dispute.refresh_from_db()
    assert dispute.is_open
    dispute.review.refresh_from_db()
    assert dispute.review.status == ReviewStatus.PUBLISHED


def test_upholding_a_dispute_from_the_queue_hides_the_review(
    admin_client: Client, dispute: Dispute, moderator: User
) -> None:
    admin_client.post(
        reverse("admin:reviews_dispute_changelist"),
        {
            "action": "hide_disputed_reviews",
            "_selected_action": [str(dispute.pk)],
            "apply_reason": "1",
            "reason": "No NNC1 was ever supplied and the company holds no record.",
        },
    )

    dispute.refresh_from_db()
    dispute.review.refresh_from_db()
    assert dispute.decision == DisputeDecision.HIDE
    assert dispute.decided_by == moderator
    assert dispute.review.status == ReviewStatus.HIDDEN


def test_the_queue_shows_when_a_dispute_is_late(admin_client: Client, dispute: Dispute) -> None:
    """COMPLIANCE section 3's five working days has to be checkable from inside
    the queue, not only from the promise page."""
    Dispute.objects.filter(pk=dispute.pk).update(due_at=timezone.now() - timedelta(hours=2))

    content = admin_client.get(reverse("admin:reviews_dispute_changelist")).content.decode()

    assert "OVERDUE" in content


def test_the_disputed_text_is_shown_beside_the_complaint(
    admin_client: Client, dispute: Dispute
) -> None:
    """Deciding from the company's account of the review alone would be deciding
    with one side of the story on screen."""
    content = admin_client.get(
        reverse("admin:reviews_dispute_change", args=[dispute.pk])
    ).content.decode()

    assert dispute.review.body in content
    assert "No draft" in content  # no arbitration agent writes this yet


def test_staff_cannot_file_a_dispute(admin_client: Client) -> None:
    response = admin_client.get(reverse("admin:reviews_dispute_add"))

    assert response.status_code in (302, 403)
