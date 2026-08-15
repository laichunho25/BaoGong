"""A company's right of appeal, and the limits on what filing one can do.

COMPLIANCE section 3 gives companies a route of appeal with a five-working-day
deadline. Most of these tests exist to keep two properties true, because both
are easy to lose to a small convenience:

* **filing changes nothing.** If a dispute hid the review, the appeal form
  would be a one-click takedown and the company with the most staff would have
  the cleanest page. The test that a disputed review stays public is the whole
  point of the feature.
* **the outcome goes through the same door as any other moderation decision** -
  named moderator, written reason, ``hide_review`` / ``remove_review`` - so
  there is no second, quieter way to unpublish something.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ProviderMember
from apps.core.dates import business_days_from
from apps.reviews import selectors, services
from apps.reviews.models import Dispute, DisputeDecision, DisputeGround, ReviewStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Review

pytestmark = pytest.mark.django_db

REASON = (
    "This reviewer has never been our client. We have no engagement record "
    "under that name and no invoice matching the period described."
)


@pytest.fixture
def provider(make_provider: Callable[..., Provider]) -> Provider:
    return make_provider()


@pytest.fixture
def review(
    provider: Provider, make_user: Callable[..., User], make_review: Callable[..., Review]
) -> Review:
    return make_review(
        provider=provider,
        author=make_user(email="buyer@example.com"),
        status=ReviewStatus.PUBLISHED,
        published_at=timezone.now(),
    )


@pytest.fixture
def member(provider: Provider, make_user: Callable[..., User]) -> User:
    user = make_user(email="secretary@example.com")
    ProviderMember.objects.create(user=user, provider=provider)
    return user


@pytest.fixture
def dispute(review: Review, member: User) -> Dispute:
    return services.raise_dispute(
        review=review,
        raised_by=member,
        ground=DisputeGround.NOT_A_CUSTOMER,
        reason=REASON,
    )


# ------------------------------------------------------------------- business days


def test_a_deadline_skips_the_weekend() -> None:
    """Friday plus five working days is the following Friday, not Wednesday."""
    friday = datetime(2026, 8, 14, 10, 0)

    assert business_days_from(friday, 5) == datetime(2026, 8, 21, 10, 0)


def test_a_deadline_starting_on_a_saturday_lands_on_a_weekday() -> None:
    saturday = datetime(2026, 8, 15, 9, 30)

    due = business_days_from(saturday, 1)
    assert due == datetime(2026, 8, 17, 9, 30)  # Monday
    assert due.weekday() == 0


def test_zero_days_is_now() -> None:
    moment = datetime(2026, 8, 15, 9, 30)

    assert business_days_from(moment, 0) == moment


def test_a_negative_deadline_is_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        business_days_from(datetime(2026, 8, 15), -1)


# ------------------------------------------------------------------- raising one


def test_raising_a_dispute_does_not_touch_the_review(review: Review, member: User) -> None:
    """The property the whole feature depends on. If this ever fails, the
    appeal form has become a takedown button."""
    services.raise_dispute(
        review=review, raised_by=member, ground=DisputeGround.NOT_A_CUSTOMER, reason=REASON
    )

    review.refresh_from_db()
    assert review.status == ReviewStatus.PUBLISHED
    assert review.is_public
    assert review.moderated_by is None


def test_the_deadline_is_five_working_days_out(dispute: Dispute, settings: Any) -> None:
    expected = business_days_from(dispute.created_at, settings.DISPUTE_SLA_BUSINESS_DAYS)

    # Same working day, allowing for the moment between create and assert.
    assert abs((dispute.due_at - expected).total_seconds()) < 5
    assert dispute.is_open
    assert not dispute.is_overdue()


def test_only_a_member_of_that_company_can_appeal(
    review: Review, make_user: Callable[..., User]
) -> None:
    stranger = make_user(email="nobody@example.com")

    with pytest.raises(services.ReviewError):
        services.raise_dispute(
            review=review, raised_by=stranger, ground=DisputeGround.OTHER, reason=REASON
        )


def test_the_author_cannot_appeal_against_their_own_review(review: Review) -> None:
    with pytest.raises(services.ReviewError):
        services.raise_dispute(
            review=review, raised_by=review.author, ground=DisputeGround.OTHER, reason=REASON
        )


def test_a_review_nobody_can_read_cannot_be_appealed(
    review: Review, member: User, moderator: User
) -> None:
    """Nothing to appeal against: it is already out of public view."""
    services.hide_review(review=review, moderator=moderator, note="Pending investigation.")

    with pytest.raises(services.ReviewError):
        services.raise_dispute(
            review=review, raised_by=member, ground=DisputeGround.OTHER, reason=REASON
        )


def test_an_empty_case_is_refused(review: Review, member: User) -> None:
    with pytest.raises(services.ReviewError):
        services.raise_dispute(
            review=review, raised_by=member, ground=DisputeGround.OTHER, reason="   "
        )


def test_an_unknown_ground_is_refused(review: Review, member: User) -> None:
    with pytest.raises(services.ReviewError):
        services.raise_dispute(
            review=review, raised_by=member, ground="we_dislike_it", reason=REASON
        )


def test_a_second_open_dispute_is_refused(dispute: Dispute, review: Review, member: User) -> None:
    """Re-filing is not more evidence, and a company could otherwise flood the
    queue for a single review."""
    with pytest.raises(services.ReviewError):
        services.raise_dispute(
            review=review, raised_by=member, ground=DisputeGround.DEFAMATORY, reason=REASON
        )


def test_the_database_enforces_one_open_dispute_too(dispute: Dispute, review: Review) -> None:
    """The service check is the message; this is the guarantee."""
    with pytest.raises(IntegrityError):
        Dispute.objects.create(
            review=review,
            provider=review.provider,
            ground=DisputeGround.OTHER,
            reason=REASON,
            due_at=timezone.now(),
        )


def test_a_new_dispute_is_allowed_once_the_first_is_closed(
    dispute: Dispute, review: Review, member: User, moderator: User
) -> None:
    """A company that appeals again after losing is filing a new complaint, not
    duplicating the old one - and the review is public again only if it stood."""
    services.decide_dispute(
        dispute=dispute,
        moderator=moderator,
        decision=DisputeDecision.KEEP,
        note="The engagement record you supplied does not cover the period described.",
    )

    again = services.raise_dispute(
        review=review, raised_by=member, ground=DisputeGround.FACTUALLY_WRONG, reason=REASON
    )
    assert again.is_open


def test_the_structured_evidence_is_kept_beside_the_prose(review: Review, member: User) -> None:
    raised = services.raise_dispute(
        review=review,
        raised_by=member,
        ground=DisputeGround.NOT_A_CUSTOMER,
        reason=REASON,
        evidence={"engagement_ref": "ENG-2024-118"},
    )

    assert raised.evidence == {"engagement_ref": "ENG-2024-118"}


# -------------------------------------------------------------------- deciding one


def test_rejecting_a_dispute_leaves_the_review_alone(dispute: Dispute, moderator: User) -> None:
    decided = services.decide_dispute(
        dispute=dispute,
        moderator=moderator,
        decision=DisputeDecision.KEEP,
        note="We checked the NNC1 on file; the reviewer was a client in that year.",
    )

    dispute.review.refresh_from_db()
    assert decided.decision == DisputeDecision.KEEP
    assert decided.decided_by == moderator
    assert dispute.review.status == ReviewStatus.PUBLISHED
    # Nothing changed about the review, so nobody's name goes on it.
    assert dispute.review.moderated_by is None


def test_upholding_a_dispute_hides_the_review_with_a_named_moderator(
    dispute: Dispute, moderator: User
) -> None:
    """Through ``hide_review``, so the review carries the same attribution as
    one hidden from the moderation queue."""
    services.decide_dispute(
        dispute=dispute,
        moderator=moderator,
        decision=DisputeDecision.HIDE,
        note="No engagement record exists and the reviewer supplied no NNC1.",
    )

    dispute.review.refresh_from_db()
    assert dispute.review.status == ReviewStatus.HIDDEN
    assert dispute.review.moderated_by == moderator
    assert dispute.review.moderation_note


def test_removing_a_disputed_review_goes_through_the_same_door(
    dispute: Dispute, moderator: User
) -> None:
    services.decide_dispute(
        dispute=dispute,
        moderator=moderator,
        decision=DisputeDecision.REMOVE,
        note="The review names a third party and their phone number.",
    )

    dispute.review.refresh_from_db()
    assert dispute.review.status == ReviewStatus.REMOVED
    assert dispute.review.moderated_by == moderator


def test_amend_hides_the_review_for_now(dispute: Dispute, moderator: User) -> None:
    """Reviews cannot be edited yet (ROADMAP), so "amend" is recorded as its own
    finding while behaving as a hide - the distinction matters the day the edit
    flow arrives."""
    decided = services.decide_dispute(
        dispute=dispute,
        moderator=moderator,
        decision=DisputeDecision.AMEND,
        note="The account of the fee is fair; the sentence about the director is not.",
    )

    dispute.review.refresh_from_db()
    assert decided.decision == DisputeDecision.AMEND
    assert dispute.review.status == ReviewStatus.HIDDEN


def test_hiding_a_disputed_review_recomputes_the_company_score(
    dispute: Dispute, moderator: User
) -> None:
    """A hidden review must stop counting immediately, or the appeal succeeded
    on the page but not in the number."""
    provider = dispute.review.provider
    # The fixture writes the review straight to the DB, so seed the cache the
    # way a moderator publishing it would have.
    services.recompute_provider_rating(str(provider.pk))
    provider.refresh_from_db()
    assert provider.rating_cached is not None

    services.decide_dispute(
        dispute=dispute,
        moderator=moderator,
        decision=DisputeDecision.HIDE,
        note="Not a client.",
    )

    provider.refresh_from_db()
    assert provider.rating_cached is None
    assert provider.verified_review_count == 0


def test_only_a_moderator_can_decide(dispute: Dispute, member: User) -> None:
    with pytest.raises(services.ReviewError):
        services.decide_dispute(
            dispute=dispute, moderator=member, decision=DisputeDecision.KEEP, note="We win."
        )


def test_a_decision_without_a_reason_is_refused(dispute: Dispute, moderator: User) -> None:
    """The note is read by the company that filed the appeal: it is the answer,
    not a log line."""
    with pytest.raises(services.ReviewError):
        services.decide_dispute(
            dispute=dispute, moderator=moderator, decision=DisputeDecision.KEEP, note=" "
        )


def test_an_unknown_decision_is_refused(dispute: Dispute, moderator: User) -> None:
    with pytest.raises(services.ReviewError):
        services.decide_dispute(
            dispute=dispute, moderator=moderator, decision="escalate", note="Sent upstairs."
        )


def test_a_closed_dispute_is_not_decided_twice(dispute: Dispute, moderator: User) -> None:
    services.decide_dispute(
        dispute=dispute, moderator=moderator, decision=DisputeDecision.KEEP, note="It stands."
    )

    with pytest.raises(services.ReviewError):
        services.decide_dispute(
            dispute=dispute,
            moderator=moderator,
            decision=DisputeDecision.REMOVE,
            note="Changed my mind.",
        )


# ---------------------------------------------------------------------- the queue


def test_the_queue_puts_the_closest_deadline_first(
    dispute: Dispute,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    """Sorted by deadline rather than arrival: the row closest to breaking the
    five-working-day promise is the one a moderator should open next."""
    other_provider = make_provider()
    other_review = make_review(
        provider=other_provider,
        author=make_user(email="another-buyer@example.com"),
        status=ReviewStatus.PUBLISHED,
        published_at=timezone.now(),
    )
    other_member = make_user(email="another-secretary@example.com")
    ProviderMember.objects.create(user=other_member, provider=other_provider)
    urgent = services.raise_dispute(
        review=other_review, raised_by=other_member, ground=DisputeGround.OTHER, reason=REASON
    )
    Dispute.objects.filter(pk=urgent.pk).update(due_at=timezone.now() - timedelta(days=1))

    assert [row.pk for row in selectors.dispute_queue()] == [urgent.pk, dispute.pk]


def test_an_overdue_dispute_is_visible_as_overdue(dispute: Dispute) -> None:
    """COMPLIANCE section 3's promise has to be checkable from the inside."""
    Dispute.objects.filter(pk=dispute.pk).update(due_at=timezone.now() - timedelta(hours=1))
    dispute.refresh_from_db()

    assert dispute.is_overdue()
    assert list(selectors.overdue_disputes()) == [dispute]


def test_a_closed_dispute_is_never_overdue(dispute: Dispute, moderator: User) -> None:
    Dispute.objects.filter(pk=dispute.pk).update(due_at=timezone.now() - timedelta(days=30))
    dispute.refresh_from_db()
    services.decide_dispute(
        dispute=dispute, moderator=moderator, decision=DisputeDecision.KEEP, note="Late, but done."
    )

    assert not dispute.is_overdue()
    assert list(selectors.overdue_disputes()) == []
    assert list(selectors.dispute_queue()) == []


def test_a_company_can_see_its_own_disputes(dispute: Dispute, provider: Provider) -> None:
    assert list(selectors.disputes_for_provider(provider)) == [dispute]


# ---------------------------------------------------------------------- the page


def test_a_member_is_offered_the_appeal_form(client: Client, review: Review, member: User) -> None:
    client.force_login(member)

    response = client.get(reverse("reviews:dispute", args=[review.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "不会隐藏" in body  # the page must say filing is not a takedown


def test_a_stranger_gets_404_rather_than_403(
    client: Client, review: Review, make_user: Callable[..., User]
) -> None:
    """Which reviews a company has is not learned by probing."""
    client.force_login(make_user(email="curious@example.com"))

    assert client.get(reverse("reviews:dispute", args=[review.pk])).status_code == 404


def test_filing_from_the_page_creates_an_open_dispute(
    client: Client, review: Review, member: User
) -> None:
    client.force_login(member)

    response = client.post(
        reverse("reviews:dispute", args=[review.pk]),
        {"ground": DisputeGround.NOT_A_CUSTOMER, "reason": REASON, "engagement_ref": "ENG-1"},
    )

    assert response.status_code == 302
    raised = Dispute.objects.get()
    assert raised.is_open
    assert raised.evidence == {"engagement_ref": "ENG-1"}
    review.refresh_from_db()
    assert review.is_public


def test_a_one_line_complaint_is_rejected_by_the_form(
    client: Client, review: Review, member: User
) -> None:
    client.force_login(member)

    response = client.post(
        reverse("reviews:dispute", args=[review.pk]),
        {"ground": DisputeGround.OTHER, "reason": "This is false."},
    )

    assert response.status_code == 200
    assert Dispute.objects.count() == 0


def test_a_second_appeal_is_redirected_rather_than_offered_a_form(
    client: Client, dispute: Dispute, member: User
) -> None:
    client.force_login(member)

    response = client.get(reverse("reviews:dispute", args=[dispute.review.pk]))

    assert response.status_code == 302


def test_the_appeal_page_is_kept_out_of_search_results(
    client: Client, review: Review, member: User
) -> None:
    client.force_login(member)

    response = client.get(reverse("reviews:dispute", args=[review.pk]))

    assert "noindex" in response.content.decode()
