"""Reads for the review layer. No writes here (ARCHITECTURE section 3).

Every public query filters on ``status=published``. That filter is the whole
safety property of this module: a review in ``pending_moderation`` has been
read by nobody, and one that was hidden after a complaint must stop being
reachable the moment it is hidden - including from the author's own pages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.reviews.models import Review, ReviewStatus

if TYPE_CHECKING:
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
    return Review.objects.filter(author=author).select_related("provider__licensee")


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
