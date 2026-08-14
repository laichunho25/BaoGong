"""Celery tasks: orchestration only, logic lives in services.py."""

from __future__ import annotations

import logging

from celery import shared_task

from apps.providers.models import ClaimEvidence
from apps.providers.services import (
    ensure_providers,
    purge_expired_evidence,
    recompute_ranking_inputs,
    scan_evidence,
)

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


@shared_task(  # type: ignore[untyped-decorator]
    name="providers.scan_claim_evidence",
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=3,
    time_limit=300,
)
def scan_claim_evidence(evidence_id: str) -> str:
    """Scan one uploaded file after it has been stored.

    Queued rather than done in the request: a scanner can take seconds, and an
    upload that times out would leave the applicant re-submitting documents.
    Until this runs the file is ``scan_pending``, which means unreadable.
    """
    evidence = ClaimEvidence.objects.filter(pk=evidence_id).first()
    if evidence is None:
        logger.warning("Evidence %s vanished before it could be scanned", evidence_id)
        return "missing"
    return str(scan_evidence(evidence).scan_status)


@shared_task(  # type: ignore[untyped-decorator]
    name="providers.purge_claim_evidence",
    autoretry_for=(Exception,),
    retry_backoff=60,
    max_retries=3,
    time_limit=900,
)
def purge_claim_evidence() -> int:
    """Delete evidence whose retention window has passed (COMPLIANCE section 4).

    A retention promise that nothing executes is worse than no promise, so this
    runs daily and logs what it removed.
    """
    purged = purge_expired_evidence()
    if purged:
        logger.info("Purged %s expired claim evidence file(s)", purged)
    return purged
