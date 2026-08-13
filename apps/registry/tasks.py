"""Celery tasks: orchestration only, logic lives in services.py."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from apps.registry.services import sync_registry

logger = logging.getLogger(__name__)


@shared_task(  # type: ignore[untyped-decorator]
    name="registry.sync_tcsp_registry",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_backoff_max=1800,
    retry_jitter=True,
    max_retries=3,
    # The register is not idempotent-unsafe, but two concurrent syncs would
    # race on the same rows; one run a day is the contract.
    time_limit=1800,
)
def sync_tcsp_registry(self: Any, *, dry_run: bool = False) -> dict[str, Any]:
    """Daily register sync. Scheduled at 06:00 Asia/Hong_Kong by beat."""
    report = sync_registry(dry_run=dry_run)
    if report.status == "aborted_sanity":
        # Do not retry: the file is intact but implausible, so retrying just
        # re-downloads the same implausible file. A human has to look.
        logger.critical("TCSP sync aborted by sanity check: %s", report.error)
    return {
        "status": report.status,
        "row_count": report.row_count,
        "prev_row_count": report.prev_row_count,
        "changes": report.changes,
        "sync_run_id": report.sync_run_id,
    }
