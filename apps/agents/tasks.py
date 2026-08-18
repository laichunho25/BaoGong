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


@shared_task(  # type: ignore[untyped-decorator]
    name="agents.match_rfq",
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=2,
    time_limit=180,
)
def match_rfq(rfq_id: str) -> str:
    """Run A2 over a requirement that has just been published.

    Queued rather than run inside ``publish_rfq``: the requirement is on the
    wall the moment it is published and companies can answer it immediately,
    which is the part the buyer is waiting for. The shortlist is a reading list
    that appears on their own page a moment later; if this never runs, the
    buyer loses a suggestion, not their requirement.

    A requirement that stopped being open before the worker got to it is left
    alone. Suggesting companies for a closed request would be work nobody can
    act on.
    """
    from apps.agents.services import match_providers as run
    from apps.rfq.models import Rfq, RfqStatus

    rfq = Rfq.objects.filter(pk=rfq_id).first()
    if rfq is None:
        logger.warning("Rfq %s vanished before it could be matched", rfq_id)
        return "missing"
    if rfq.status != RfqStatus.OPEN:
        return "skipped"

    result = run(rfq)
    if result is None:
        return "empty"
    return "fallback" if result.used_fallback else "ok"


@shared_task(  # type: ignore[untyped-decorator]
    name="agents.analyse_quote",
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=2,
    time_limit=180,
)
def analyse_quote(quote_id: str) -> str:
    """Run A5 over a quote that has just been submitted.

    Queued rather than run inside ``submit_quote``: the company pressing Send
    has spent one of its three daily quotes and is owed a fast answer, and the
    buyer's comparison table works without the analysis - the missing-item list
    on that page is computed from the standard labels either way. If this never
    runs, the buyer loses the questions, not the quote.
    """
    from apps.agents.services import analyse_quote as run
    from apps.rfq.models import Quote

    quote = (
        Quote.objects.select_related("rfq")
        .prefetch_related("line_items")
        .filter(pk=quote_id)
        .first()
    )
    if quote is None:
        logger.warning("Quote %s vanished before it could be analysed", quote_id)
        return "missing"

    result = run(quote)
    if result is None:
        return "skipped"
    return "fallback" if result.used_fallback else "ok"


@shared_task(  # type: ignore[untyped-decorator]
    name="agents.summarise_registry_diff",
    autoretry_for=(Exception,),
    retry_backoff=60,
    max_retries=2,
    time_limit=600,
)
def summarise_registry_diff(sync_run_id: str) -> str:
    """Run A7 over the differences one sync run found.

    Chained after the sync rather than folded into it. The sync writes official
    data under a write gate (CLAUDE.md rule 1) and must not be retried because
    a summary failed - re-downloading and re-diffing the register to recover a
    paragraph of prose would be the tail wagging the dog.

    A run with no differences returns "empty" and mails nobody.
    """
    from apps.agents.services import summarise_registry_diff as run
    from apps.registry.models import SyncRun

    sync_run = SyncRun.objects.filter(pk=sync_run_id).first()
    if sync_run is None:
        logger.warning("SyncRun %s vanished before its diff could be summarised", sync_run_id)
        return "missing"

    digest = run(sync_run)
    if digest is None:
        return "empty"
    return "fallback" if digest.used_fallback else "ok"
