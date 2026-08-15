"""Celery tasks: orchestration only, logic lives in services.py."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(  # type: ignore[untyped-decorator]
    name="agents.moderate_review",
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=2,
    time_limit=180,
)
def moderate_review(review_id: str) -> str:
    """Run A4 over a newly submitted review.

    Queued, not synchronous: the reviewer has already been told their review is
    awaiting moderation, and holding their request open for a model call would
    buy them nothing. Nothing downstream waits on this either - the review is
    already in the queue, and this only decides how the queue is sorted.

    Imports inside the function because the agent modules pull in the reviews
    app, and Celery autodiscovery imports this at startup.
    """
    from apps.agents.services import moderate_review as run
    from apps.reviews.models import Review

    review = Review.objects.select_related("provider", "author").filter(pk=review_id).first()
    if review is None:
        logger.warning("Review %s vanished before it could be moderated", review_id)
        return "missing"

    result = run(review)
    return "fallback" if result.used_fallback else "ok"


@shared_task(  # type: ignore[untyped-decorator]
    name="agents.extract_nnc1",
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=2,
    time_limit=300,
)
def extract_nnc1(verification_id: str) -> str:
    """Run A3 over a scanned NNC1.

    Chained after ``reviews.process_nnc1`` rather than merged into it: the scan
    and the rule-based name match are what a moderator needs to act, and they
    must not wait behind a vision call - nor be retried when only the vision
    call failed.
    """
    from apps.agents.services import extract_nnc1 as run
    from apps.reviews.models import Nnc1Verification

    verification = Nnc1Verification.objects.filter(pk=verification_id).first()
    if verification is None:
        logger.warning("NNC1 %s vanished before it could be read", verification_id)
        return "missing"

    result = run(verification)
    if result is None:
        return "skipped"
    return "fallback" if result.used_fallback else "ok"
