"""Reads for the review layer. No writes here (ARCHITECTURE section 3).

Every public query filters on ``status=published``. That filter is the whole
safety property of this module: a review in ``pending_moderation`` has been
read by nobody, and one that was hidden after a complaint must stop being
reachable the moment it is hidden - including from the author's own pages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils import timezone

from apps.reviews.models import Dispute, Nnc1Verification, Review, ReviewStatus, VerificationResult

if TYPE_CHECKING:
    from datetime import datetime

    from django.db.models import QuerySet

    from apps.accounts.models import User
    from apps.providers.models import Provider


def published_reviews(provider: Provider) -> QuerySet[Review]:
    """What the detail page lists: newest first, with the company's reply."""
    return (
        Review.objects.filter(provider=provider, status=ReviewStatus.PUBLISHED)
        .select_related("score", "reply")
        .order_by("-published_at", "-created_at")
    )


def featured_reviews(*, limit: int = 3) -> QuerySet[Review]:
    """Published, NNC1-verified reviews for the home page, newest first.

    Verified only, and not for presentation: the home page is where the
    platform makes its claim about reviews, so the reviews it shows there have
    to be the ones that claim covers. A pending or unverified review on the
    front page would advertise a standard the page itself does not meet.

    Nothing here is picked by hand or by score - the ordering is time, so the
    section cannot quietly become a place where flattering reviews live.
    """
    return (
        Review.objects.filter(status=ReviewStatus.PUBLISHED, is_verified=True)
        .select_related("provider__licensee", "score")
        .order_by("-published_at", "-created_at")[:limit]
    )


def review_by_author(*, provider: Provider, author: User) -> Review | None:
    """The one review this account may have left for this company.

    Used to decide whether to offer the form at all - the unique constraint is
    the guarantee, this is the courtesy of not showing a form that will fail.
    """
    if not author.is_authenticated:
        return None
    return Review.objects.filter(provider=provider, author=author).first()


def reviews_by_author(author: User) -> QuerySet[Review]:
    """The author's own reviews, in any status, for their dashboard.

    Authors see their own pending and hidden reviews: being told "this is not
    public and here is why" is the minimum owed to someone who wrote something.
    """
    return Review.objects.filter(author=author).select_related("provider__licensee", "verification")


def get_review(review_id: str) -> Review | None:
    return (
        Review.objects.select_related("provider__licensee", "author", "score", "reply")
        .filter(pk=review_id)
        .first()
    )


def moderation_queue() -> QuerySet[Review]:
    return (
        Review.objects.filter(status=ReviewStatus.PENDING_MODERATION)
        .select_related("provider__licensee", "author", "score")
        .order_by("created_at")
    )


def verification_queue() -> QuerySet[Nnc1Verification]:
    """Uploaded NNC1s waiting on a human, oldest first.

    Unlike the review queue this one is not drainable by rule: every row here
    needs somebody to open a document (``matching.py`` explains why a name
    match cannot stand in for that), so its length is a staffing number.
    """
    return (
        Nnc1Verification.objects.filter(result=VerificationResult.NEEDS_HUMAN)
        .select_related("review__provider__licensee", "review__author")
        .order_by("created_at")
    )


def verification_for(review: Review) -> Nnc1Verification | None:
    return Nnc1Verification.objects.filter(review=review).first()


def dispute_queue() -> QuerySet[Dispute]:
    """Open disputes, soonest deadline first.

    Ordered by ``due_at`` rather than by arrival, so the row closest to
    breaking COMPLIANCE section 3's five-working-day promise is the one on top.
    """
    return (
        Dispute.objects.filter(decision="")
        .select_related("review__provider__licensee", "review__author", "provider")
        .order_by("due_at")
    )


def overdue_disputes(*, now: datetime | None = None) -> QuerySet[Dispute]:
    """Open disputes past their deadline - the promise already broken.

    Separate from ``dispute_queue`` because the two answer different questions:
    one is what to work on next, this one is what to explain.
    """
    return dispute_queue().filter(due_at__lt=now or timezone.now())


def open_dispute_for(review: Review) -> Dispute | None:
    return Dispute.objects.filter(review=review, decision="").first()


def disputes_for_provider(provider: Provider) -> QuerySet[Dispute]:
    """A company's own disputes, newest first, for its dashboard."""
    qs = Dispute.objects.filter(provider=provider).select_related("review")
    return qs.order_by("-created_at")
