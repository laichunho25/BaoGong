"""Writes. Every agent result reaches the database through this module.

CLAUDE.md rule 3 is the whole design here: nothing below changes a status, a
score, or a verification outcome. ``moderate_review`` writes to
``Review.moderation``; ``extract_nnc1`` writes to ``Nnc1Verification.extracted``.
Both of those columns exist precisely so that an agent has somewhere to put an
opinion that is not the same place a decision lives.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.agents import review_moderation
from apps.agents.models import AgentFeedback, AgentRun, FeedbackVerdict
from apps.agents.nnc1_extraction import Nnc1ExtractionAgent
from apps.agents.review_moderation import ReviewModerationAgent
from apps.agents.schemas import ModerationOut, Nnc1Out

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.agents.base import AgentResult
    from apps.reviews.models import Nnc1Verification, Review

logger = logging.getLogger(__name__)


class AgentServiceError(Exception):
    """A caller asked for something this module must refuse."""


@transaction.atomic
def moderate_review(review: Review) -> AgentResult:
    """Attach A4's read to ``review.moderation``. Advice only.

    Runs on a review that is already ``pending_moderation`` and stays there:
    the field this writes is displayed beside the review in the moderator's
    queue, and is the only thing an agent contributes to that decision.
    """
    agent = ReviewModerationAgent()
    result = agent.run(
        {
            "object_id": str(review.pk),
            "body": review.body,
            "provider_name": review.provider.display_name,
            "services": list(review.service_used),
            "overall": str(review.overall),
            "author_verified": review.author.is_email_verified,
        }
    )
    data = result.data
    if not isinstance(data, ModerationOut):  # pragma: no cover - schema is fixed
        raise AgentServiceError("moderation agent returned the wrong schema")

    review.moderation = {
        **data.model_dump(mode="json"),
        "model": agent.model,
        "prompt_version": agent.prompt_version,
        "run_id": result.run_id,
        "used_fallback": result.used_fallback,
        "fallback_reason": result.fallback_reason,
        "escalation_reason": review_moderation.escalation_reason(
            data, used_fallback=result.used_fallback
        ),
    }
    review.save(update_fields=["moderation", "updated_at"])
    return result


@transaction.atomic
def extract_nnc1(verification: Nnc1Verification) -> AgentResult | None:
    """Read the uploaded document with A3 and store the reading as advice.

    Returns ``None`` without calling anything when the file is not readable.
    An unscanned or quarantined upload is not opened to be sent somewhere else
    any more than it is opened to be shown to a moderator, and a decided
    verification has nothing left to learn from a second reading.
    """
    if not verification.is_readable:
        logger.info("skipping NNC1 extraction for %s: file not readable", verification.pk)
        return None
    if verification.is_decided:
        return None

    try:
        with verification.file.open("rb") as handle:
            content = handle.read()
    except (OSError, ValueError):
        logger.exception("could not read NNC1 %s for extraction", verification.pk)
        return None

    result = Nnc1ExtractionAgent().run(
        {
            "verification_id": str(verification.pk),
            "media_type": verification.content_type,
            "content": content,
        }
    )
    data = result.data
    if not isinstance(data, Nnc1Out):  # pragma: no cover - schema is fixed
        raise AgentServiceError("extraction agent returned the wrong schema")

    verification.extracted = {
        **data.model_dump(mode="json"),
        "used_fallback": result.used_fallback,
        "fallback_reason": result.fallback_reason,
    }
    verification.extraction_confidence = Decimal(str(round(result.confidence, 2)))
    verification.agent_run_id_ref = result.run_id
    verification.save(
        update_fields=[
            "extracted",
            "extraction_confidence",
            "agent_run_id_ref",
            "updated_at",
        ]
    )
    return result


def record_feedback(
    *, agent_run: AgentRun, reviewer: User, verdict: str, notes: str = ""
) -> AgentFeedback:
    """A moderator's verdict on one run - the only non-synthetic eval data.

    Upserted per reviewer so that changing one's mind corrects the record
    rather than adding a second, contradictory row.
    """
    if verdict not in FeedbackVerdict.values:
        raise AgentServiceError(_("未知的评价结果。"))

    feedback, _created = AgentFeedback.objects.update_or_create(
        agent_run=agent_run,
        reviewer=reviewer,
        defaults={"verdict": verdict, "notes": notes.strip()},
    )
    return feedback
