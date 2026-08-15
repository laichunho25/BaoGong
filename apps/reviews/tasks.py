"""Celery tasks: orchestration only, logic lives in services.py."""

from __future__ import annotations

import logging

from celery import shared_task

from apps.agents.tasks import extract_nnc1
from apps.providers.models import Provider
from apps.reviews.models import Nnc1Verification
from apps.reviews.services import (
    purge_expired_nnc1,
    recompute_provider_rating,
    run_name_match,
    scan_nnc1,
)

logger = logging.getLogger(__name__)


@shared_task(  # type: ignore[untyped-decorator]
    name="reviews.recompute_provider_rating",
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=3,
    time_limit=300,
)
def recompute_rating(provider_id: str) -> str | None:
    """Refresh one company's cached rating.

    Moderation decisions call the service directly and synchronously - a
    published review that is not yet reflected in the score would be visible
    proof that the two disagree. This task exists for the paths where that is
    not possible: bulk fixes, and P4-2's verification result arriving from a
    worker.
    """
    provider = recompute_provider_rating(provider_id)
    if provider is None:
        logger.warning("Provider %s vanished before its rating could be recomputed", provider_id)
        return None
    return str(provider.rating_cached)


@shared_task(  # type: ignore[untyped-decorator]
    name="reviews.recompute_all_ratings",
    autoretry_for=(Exception,),
    retry_backoff=60,
    max_retries=1,
    time_limit=3600,
)
def recompute_all_ratings() -> int:
    """Rebuild every cached rating from the reviews themselves.

    The formula is a product decision that will change (RATING_SYSTEM section 2
    already describes a v2), and when it does, every cached number on the site
    is wrong until something recomputes it. Kept out of the beat schedule: it
    is a deliberate migration step, not a daily chore.
    """
    recomputed = 0
    for provider_id in Provider.objects.filter(rating_count__gt=0).values_list("pk", flat=True):
        recompute_provider_rating(str(provider_id))
        recomputed += 1
    logger.info("Recomputed %s provider rating(s)", recomputed)
    return recomputed


@shared_task(  # type: ignore[untyped-decorator]
    name="reviews.process_nnc1",
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=3,
    time_limit=300,
)
def process_nnc1(verification_id: str) -> str:
    """Scan an uploaded NNC1, then compare its declared secretary to the register.

    Queued rather than done in the request: a scan takes seconds, and an upload
    that times out leaves someone re-submitting a document that is already in
    the bucket. Until this runs the file is ``scan_pending``, which means
    unreadable and unpassable.

    The name match runs even when the scan fails. It reads columns the uploader
    typed, not the file, so it costs nothing and gives a moderator looking at a
    quarantined upload something to go on.
    """
    verification = Nnc1Verification.objects.filter(pk=verification_id).first()
    if verification is None:
        logger.warning("NNC1 %s vanished before it could be processed", verification_id)
        return "missing"

    scan_nnc1(verification)
    run_name_match(verification)

    # A3 reads the document only once it is known to be safe to open (P4-3).
    # A separate task so that a failing vision call cannot cause the scan and
    # the name match - the two things a moderator actually needs - to be run
    # again from the top.
    if verification.is_readable:
        extract_nnc1.delay(str(verification.pk))
    return str(verification.scan_status)


@shared_task(  # type: ignore[untyped-decorator]
    name="reviews.purge_nnc1_documents",
    autoretry_for=(Exception,),
    retry_backoff=60,
    max_retries=3,
    time_limit=900,
)
def purge_nnc1_documents() -> int:
    """Delete decided NNC1 documents past their retention window.

    COMPLIANCE section 4. An NNC1 carries more personal data than anything else
    the platform accepts - directors, addresses, identity numbers - so this is
    the daily job whose silent failure would matter most.
    """
    purged = purge_expired_nnc1()
    if purged:
        logger.info("Purged %s expired NNC1 document(s)", purged)
    return purged
