"""Writes to the review layer, and the score the whole platform is judged on.

The rating formula is RATING_SYSTEM sections 1-3. Two properties of it matter
more than the arithmetic:

* the Bayesian prior (10 imaginary five-star reviews) stops one angry review
  from reading as 1.0, but it also means a company with no reviews computes to
  exactly 5.00. That number must never be shown, so ``recompute_provider_rating``
  writes **null** when there are no verified reviews rather than the prior's
  value - the display rule in RATING_SYSTEM section 4 then has something it can
  actually test for.
* v1 weights are "verified 1.0 / unverified 0.0". Unverified reviews are shown
  and counted in ``rating_count``, and contribute nothing to the score. Time
  decay (weight 0.5 beyond 24 months) is deliberately not implemented yet: it
  changes every score on the site the day it ships, and there is no volume to
  justify it.

Moderation decisions live here too, for the same reason claim decisions do: the
admin is one caller among several, so "a moderator did it and wrote a reason"
cannot be bypassed by coming through a different door.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.permissions import is_moderator, is_provider_member
from apps.providers.models import Provider
from apps.providers.services import recompute_ranking_inputs
from apps.reviews.models import SCORE_FIELDS, Review, ReviewReply, ReviewScore, ReviewStatus

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from apps.accounts.models import User

# RATING_SYSTEM section 1.
PRIOR_COUNT = Decimal("10")
PRIOR_MEAN = Decimal("5.0")

#: RATING_SYSTEM section 2 v1. Named so the v2 weights have somewhere to go.
WEIGHT_VERIFIED = Decimal("1.0")
WEIGHT_UNVERIFIED = Decimal("0.0")

DISPLAY_PRECISION = Decimal("0.01")
SCORE_PRECISION = Decimal("0.1")


class ReviewError(Exception):
    """A review action that must not proceed. Message is shown to the user."""


def score_overall(scores: Mapping[str, Decimal | None]) -> Decimal:
    """One review's overall score: the mean of the dimensions actually rated.

    A null ``bank_support`` ("did not use the service") drops out of the mean
    instead of counting as zero - RATING_SYSTEM section 3. Averaging over four
    dimensions is what the product asks for; averaging over five with a zero
    would silently deduct a full point from every company that never opened an
    account for that buyer.
    """
    given = [value for value in scores.values() if value is not None]
    if not given:
        raise ReviewError(_("请至少为一个维度评分。"))
    mean = sum(given, Decimal("0")) / Decimal(len(given))
    return mean.quantize(SCORE_PRECISION, rounding=ROUND_HALF_UP)


@transaction.atomic
def submit_review(
    *,
    provider: Provider,
    author: User,
    body: str,
    scores: Mapping[str, Decimal | None],
    service_used: Sequence[str] = (),
    engagement_year: int | None = None,
) -> Review:
    """Record a review. It is not public until a moderator publishes it.

    Refusing a company's own members here is the cheap half of RATING_SYSTEM
    section 6: self-reviews from a signed-in member are the one form of
    astroturfing the platform can detect for certain, so it does not need a
    model to catch it.
    """
    if is_provider_member(author, provider):
        raise ReviewError(_("您是该公司的成员，不能评价自己的公司。"))

    overall = score_overall(scores)
    try:
        with transaction.atomic():
            review = Review.objects.create(
                provider=provider,
                author=author,
                body=body.strip(),
                service_used=list(service_used),
                engagement_year=engagement_year,
                overall=overall,
                status=ReviewStatus.PENDING_MODERATION,
            )
    except IntegrityError as exc:
        # The unique constraint, not a race we can recover from: an edit flow
        # would be a different feature with a different audit trail.
        raise ReviewError(_("您已评价过这家公司。")) from exc

    ReviewScore.objects.create(
        review=review, **{field: scores.get(field) for field in SCORE_FIELDS}
    )
    return review


def _decide(*, review: Review, moderator: User, status: str, note: str) -> Review:
    if not is_moderator(moderator):
        raise ReviewError(_("只有审核人员可以处理评价。"))
    if not note.strip():
        # A published or hidden review is a statement about a named company;
        # somebody has to be able to answer "on what grounds" months later.
        raise ReviewError(_("请填写处理理由。"))

    review.status = status
    review.moderation_note = note.strip()
    review.moderated_by = moderator
    review.moderated_at = timezone.now()
    if status == ReviewStatus.PUBLISHED and review.published_at is None:
        review.published_at = timezone.now()
    review.save(
        update_fields=[
            "status",
            "moderation_note",
            "moderated_by",
            "moderated_at",
            "published_at",
            "updated_at",
        ]
    )
    recompute_provider_rating(str(review.provider_id))
    return review


def publish_review(*, review: Review, moderator: User, note: str) -> Review:
    return _decide(review=review, moderator=moderator, status=ReviewStatus.PUBLISHED, note=note)


def hide_review(*, review: Review, moderator: User, note: str) -> Review:
    """Take a review out of public view without destroying it.

    COMPLIANCE section 3: hidden, not deleted. The author keeps their text and
    the dispute trail keeps its subject.
    """
    return _decide(review=review, moderator=moderator, status=ReviewStatus.HIDDEN, note=note)


def remove_review(*, review: Review, moderator: User, note: str) -> Review:
    """For content that must not exist - personal data, illegal material."""
    return _decide(review=review, moderator=moderator, status=ReviewStatus.REMOVED, note=note)


@transaction.atomic
def reply_to_review(*, review: Review, author: User, body: str) -> ReviewReply:
    """The company's answer, published immediately.

    COMPLIANCE section 3 gives companies a right of reply; making them wait in
    the same queue as the review they are answering would make that right worth
    less than the accusation. The reply is still subject to takedown.
    """
    provider = review.provider
    if not is_provider_member(author, provider):
        raise ReviewError(_("只有该公司的成员可以回复。"))
    if not review.is_public:
        raise ReviewError(_("该评价尚未公开，暂时无法回复。"))
    if not body.strip():
        raise ReviewError(_("回复内容不能为空。"))
    if ReviewReply.objects.filter(review=review).exists():
        raise ReviewError(_("每条评价只能回复一次。"))

    return ReviewReply.objects.create(
        review=review,
        provider=provider,
        author=author,
        body=body.strip(),
        published_at=timezone.now(),
    )


def bayesian_rating(*, weighted_sum: Decimal, weight_total: Decimal) -> Decimal | None:
    """RATING_SYSTEM section 1, with the section 4 display rule folded in.

    Returns None - not 5.00 - when nothing carries weight. The prior exists to
    damp small samples, not to hand a score to a company nobody has reviewed.
    """
    if weight_total <= 0:
        return None
    rating = (PRIOR_COUNT * PRIOR_MEAN + weighted_sum) / (PRIOR_COUNT + weight_total)
    return rating.quantize(DISPLAY_PRECISION, rounding=ROUND_HALF_UP)


def recompute_provider_rating(provider_id: str) -> Provider | None:
    """Refresh one company's cached rating and counts, then its ranking inputs.

    Called after every decision that can change what is public. It reads the
    reviews rather than adjusting a running total, so a bug in one transition
    cannot leave a permanently wrong number behind.
    """
    provider = Provider.objects.filter(pk=provider_id).first()
    if provider is None:
        return None

    published = Review.objects.filter(provider=provider, status=ReviewStatus.PUBLISHED)
    stats = published.aggregate(
        total=Count("id"),
        verified=Count("id", filter=Q(is_verified=True)),
        verified_sum=Sum("overall", filter=Q(is_verified=True)),
    )
    verified_count = Decimal(stats["verified"] or 0)

    provider.rating_count = stats["total"] or 0
    provider.verified_review_count = int(verified_count)
    provider.rating_cached = bayesian_rating(
        weighted_sum=WEIGHT_VERIFIED * Decimal(stats["verified_sum"] or 0),
        weight_total=WEIGHT_VERIFIED * verified_count,
    )
    provider.save(
        update_fields=["rating_count", "verified_review_count", "rating_cached", "updated_at"]
    )

    # The ranking mixes the rating with four other inputs, so it is stale the
    # moment the rating moves.
    recompute_ranking_inputs(provider_ids=[str(provider.pk)])
    return provider


def dimension_ratings(provider: Provider) -> dict[str, Decimal | None]:
    """Per-dimension scores for the radar chart, same formula as the total.

    Only verified reviews, so the chart and the headline number are made of the
    same evidence; a dimension nobody rated stays None rather than becoming the
    prior's 5.00.
    """
    rows = ReviewScore.objects.filter(
        review__provider=provider,
        review__status=ReviewStatus.PUBLISHED,
        review__is_verified=True,
    )
    aggregates = rows.aggregate(
        **{f"{field}_avg": Avg(field) for field in SCORE_FIELDS},
        **{f"{field}_n": Count(field) for field in SCORE_FIELDS},
    )

    ratings: dict[str, Decimal | None] = {}
    for field in SCORE_FIELDS:
        count = Decimal(aggregates[f"{field}_n"] or 0)
        mean = aggregates[f"{field}_avg"]
        ratings[field] = bayesian_rating(
            weighted_sum=Decimal(str(mean)) * count if mean is not None else Decimal("0"),
            weight_total=count,
        )
    return ratings
