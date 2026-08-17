"""Celery tasks: orchestration only, logic lives in services.py."""

from __future__ import annotations

import logging

from celery import shared_task

from apps.rfq.services import expire_open_rfqs, expire_stale_quotes

logger = logging.getLogger(__name__)


@shared_task(  # type: ignore[untyped-decorator]
    name="rfq.expire_open_rfqs",
    autoretry_for=(Exception,),
    retry_backoff=60,
    max_retries=3,
    time_limit=300,
)
def expire_rfqs() -> int:
    """Take requests off the wall once their deadline has passed.

    The wall filters on the deadline as well, so this task is not what keeps it
    honest to a visitor. What it keeps honest is everything that counts rows:
    the buyer's own list, the company's dashboard, and any future report of how
    many requests went unanswered.
    """
    expired = expire_open_rfqs()
    if expired:
        logger.info("Expired %s open RFQ(s)", expired)
    return expired


@shared_task(  # type: ignore[untyped-decorator]
    name="rfq.expire_stale_quotes",
    autoretry_for=(Exception,),
    retry_backoff=60,
    max_retries=3,
    time_limit=300,
)
def expire_quotes() -> int:
    """Retire offers past the validity period the company itself set.

    Separate task from the RFQ sweep although both run hourly: a request going
    off the wall and an offer lapsing are different promises to different
    people, and one failing should not stop the other.
    """
    expired = expire_stale_quotes()
    if expired:
        logger.info("Expired %s stale quote(s)", expired)
    return expired
