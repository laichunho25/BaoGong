"""Celery tasks: orchestration only, logic lives in services.py."""

from __future__ import annotations

import logging

from celery import shared_task

from apps.providers.models import Provider
from apps.reviews.services import recompute_provider_rating

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
