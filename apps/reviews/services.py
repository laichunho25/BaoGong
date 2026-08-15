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

The NNC1 half at the bottom of the file is what puts weight behind any of it.
``decide_verification`` is the **only** writer of ``Review.is_verified``: an
uploaded document, a rule-based name match and a named moderator, in that
order. Nothing automatic sets the flag, because a badge nobody stands behind is
worth less than no badge.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.permissions import is_moderator, is_provider_member
from apps.core.scanning import ScanStatus, get_scanner
from apps.providers.models import Provider
from apps.providers.services import recompute_ranking_inputs
from apps.reviews import matching
from apps.reviews.models import (
    SCORE_FIELDS,
    Nnc1Verification,
    Review,
    ReviewReply,
    ReviewScore,
    ReviewStatus,
    VerificationResult,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from django.core.files.uploadedfile import UploadedFile

    from apps.accounts.models import User
    from apps.core.uploads import InspectedUpload

# RATING_SYSTEM section 1.
PRIOR_COUNT = Decimal("10")
PRIOR_MEAN = Decimal("5.0")

#: RATING_SYSTEM section 2 v1. Named so the v2 weights have somewhere to go.
WEIGHT_VERIFIED = Decimal("1.0")
WEIGHT_UNVERIFIED = Decimal("0.0")

DISPLAY_PRECISION = Decimal("0.01")
SCORE_PRECISION = Decimal("0.1")

SHA256_CHUNK = 64 * 1024


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


# --------------------------------------------------------------- NNC1 verification


def _digest(upload: UploadedFile[Any]) -> str:
    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in upload.chunks(SHA256_CHUNK):
        digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest()


@transaction.atomic
def submit_nnc1(
    *,
    review: Review,
    uploader: User,
    upload: UploadedFile[Any],
    inspected: InspectedUpload,
    declared_company_name: str = "",
    declared_company_no: str = "",
    declared_secretary_name: str = "",
) -> Nnc1Verification:
    """Store the document behind a verification request.

    Only the review's own author may upload: the badge says "this reviewer was
    a client", so a document supplied by anyone else proves the wrong thing.

    The row lands unscanned, which means unreadable, and ``needs_human``, which
    means it changes nothing about the review yet. Replacing an earlier upload
    is allowed while the case is undecided - people photograph the wrong page -
    but not afterwards, because that would let a passed verification be swapped
    for a different document after the fact.
    """
    if review.author_id != uploader.pk:
        raise ReviewError(_("只有评价作者本人可以上传核验文件。"))

    existing = Nnc1Verification.objects.filter(review=review).first()
    if existing is not None:
        if existing.is_decided:
            raise ReviewError(_("该评价的核验已有结论，如需申诉请联系客服。"))
        if existing.file:
            existing.file.delete(save=False)
        existing.delete()

    verification = Nnc1Verification(
        review=review,
        original_filename=(upload.name or "")[:255],
        content_type=inspected.content_type,
        extension=inspected.extension,
        size_bytes=inspected.size_bytes,
        sha256=_digest(upload),
        scan_status=ScanStatus.PENDING,
        declared_company_name=declared_company_name.strip()[:255],
        declared_company_no=declared_company_no.strip()[:32],
        declared_secretary_name=declared_secretary_name.strip()[:255],
    )
    # ``nnc1_path`` reads instance.pk and instance.extension, both set above.
    verification.file.save(f"nnc1.{inspected.extension}", upload, save=False)
    verification.save()
    return verification


def scan_nnc1(verification: Nnc1Verification) -> Nnc1Verification:
    """Run the configured scanner and record the verdict.

    Nothing here can turn a failure into a pass; a file the scanner cannot
    clear - including because no scanner is configured - stays unreadable, and
    ``decide_verification`` refuses to pass a document nobody could open.
    """
    if verification.purged_at is not None or not verification.file:
        return verification

    scanner = get_scanner()
    with verification.file.open("rb") as handle:
        result = scanner.scan(iter(lambda: handle.read(SHA256_CHUNK), b""))

    verification.scan_status = result.status
    verification.scan_detail = result.detail[:255]
    verification.scanner = result.scanner[:32]
    verification.scanned_at = timezone.now()
    verification.save(
        update_fields=["scan_status", "scan_detail", "scanner", "scanned_at", "updated_at"]
    )
    return verification


def run_name_match(verification: Nnc1Verification) -> Nnc1Verification:
    """Compare the declared secretary with the register, and record the result.

    Evidence for a moderator, never a decision - ``matching.py`` explains the
    asymmetry. When the declared name does not look like the company under
    review, the register is searched for whoever it *does* look like, because
    "this names a different licensed TCSP" and "this names nobody licensed at
    all" send a moderator down very different paths.
    """
    licensee = verification.review.provider.licensee
    if licensee is None:
        # A company page whose register row has gone (a delisting reconciled
        # badly, say). There is nothing to compare against, and inventing a
        # match here would be the one thing this function must never do.
        return verification
    match = matching.match_against_provider(verification.declared_secretary_name, licensee=licensee)

    if match.is_plausible:
        detail = f"Matches {match.matched_name}"
    else:
        elsewhere = matching.find_licensee(verification.declared_secretary_name)
        detail = (
            f"Names a different licensee: {elsewhere.matched_name} ({elsewhere.licence_no})"
            if elsewhere.is_plausible
            else "No licensed company matches the declared secretary"
        )

    verification.match_method = match.method
    verification.match_score = Decimal(str(round(match.score, 3)))
    verification.matched_licence_no = match.licence_no
    verification.match_detail = detail[:255]
    verification.save(
        update_fields=[
            "match_method",
            "match_score",
            "matched_licence_no",
            "match_detail",
            "updated_at",
        ]
    )
    return verification


@transaction.atomic
def decide_verification(
    *,
    verification: Nnc1Verification,
    reviewer: User,
    passed: bool,
    note: str,
) -> Nnc1Verification:
    """A moderator's decision on one uploaded NNC1.

    This is the only writer of ``Review.is_verified``, and therefore the only
    thing that can put weight behind a score. Three refusals:

    * not a moderator - the badge is the platform's assertion, not a user's;
    * no reason - "why is this company rated 4.9" has to be answerable;
    * passing on a document nobody could open. A verification granted on an
      unscanned or purged file is a badge backed by nothing, which is worse
      than no badge at all. Failing one is always allowed.

    The retention clock starts here rather than at upload: the document has now
    done its job, and COMPLIANCE section 4 gives it 90 days from that point.
    """
    if not is_moderator(reviewer):
        raise ReviewError(_("只有审核人员可以处理核验。"))
    if not note.strip():
        raise ReviewError(_("请填写处理理由。"))
    if passed and not verification.is_readable:
        raise ReviewError(_("该文件尚未通过病毒扫描或已被清除，不能据此通过核验。"))

    now = timezone.now()
    verification.result = VerificationResult.PASSED if passed else VerificationResult.FAILED
    verification.review_note = note.strip()
    verification.reviewed_by = reviewer
    verification.reviewed_at = now
    verification.purge_at = now + timedelta(days=settings.NNC1_RETENTION_DAYS)
    verification.save(
        update_fields=[
            "result",
            "review_note",
            "reviewed_by",
            "reviewed_at",
            "purge_at",
            "updated_at",
        ]
    )

    review = verification.review
    review.is_verified = passed
    review.save(update_fields=["is_verified", "updated_at"])
    recompute_provider_rating(str(review.provider_id))
    return verification


def purge_expired_nnc1(*, now: datetime | None = None) -> int:
    """Delete the bytes of documents whose retention window has passed.

    The row survives with its hash, its match result and the reviewer's note:
    what must not be kept is the document, while the record that a badge was
    granted on it is exactly what an audit asks for. A verification does not
    lapse when its document is purged - re-verifying every reviewer every 90
    days would be a promise the platform cannot keep.
    """
    moment = timezone.now() if now is None else now
    purged = 0
    queryset = Nnc1Verification.objects.filter(
        purge_at__isnull=False, purge_at__lte=moment, purged_at__isnull=True
    )
    for verification in queryset.iterator(chunk_size=200):
        if verification.file:
            verification.file.delete(save=False)
        verification.purged_at = moment
        verification.save(update_fields=["file", "purged_at", "updated_at"])
        purged += 1
    return purged
