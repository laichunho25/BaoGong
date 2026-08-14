"""Celery tasks: orchestration only, logic lives in services.py."""

from __future__ import annotations

import logging

from celery import shared_task

from apps.providers.services import ensure_providers, recompute_ranking_inputs

logger = logging.getLogger(__name__)


@shared_task(  # type: ignore[untyped-decorator]
    name="providers.backfill_providers",
    autoretry_for=(Exception,),
    retry_backoff=60,
    max_retries=3,
    time_limit=900,
)
def backfill_providers() -> dict[str, int]:
    """Give newly-listed licensees a directory page.

    Runs on its own schedule half an hour after the register sync rather than
    inside it. The sync must stay a single-purpose transaction over official
    data: a failure in the platform layer cannot be allowed to roll back, or
    even to delay, the mirror of the official file.
    """
    report = ensure_providers()
    rescored = recompute_ranking_inputs()
    if report.created:
        logger.info("Created %s provider page(s) for new licensees", report.created)
    return {"created": report.created, "skipped": report.skipped, "rescored": rescored}
