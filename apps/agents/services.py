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
from apps.agents.quote_analysis import QuoteAnalysisAgent
from apps.agents.review_moderation import ReviewModerationAgent
from apps.agents.rfq_intake import RfqIntakeAgent
from apps.agents.schemas import ModerationOut, Nnc1Out, QuoteAnalysisOut, RfqIntakeOut
from apps.core.money import Money

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.agents.base import AgentResult
    from apps.reviews.models import Nnc1Verification, Review
    from apps.rfq.models import Quote

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


def draft_rfq_prefill(*, raw_input: str) -> AgentResult:
    """Read a buyer's paragraph with A1 and hand back form values.

    **Writes nothing but the ``AgentRun``.** The result of this call is a
    pre-filled form; the buyer corrects it and presses Submit, and only then
    does ``rfq.services.create_rfq`` write an ``Rfq`` with ``structured`` set to
    what the model produced (CLAUDE.md rule 3, AI_AGENTS A1). Nothing here can
    put a requirement in front of a licensed company.

    Called synchronously, from the request the buyer is waiting on: the whole
    feature is "press this and the form fills in", and a queued version of that
    is a page that does nothing.
    """
    result = RfqIntakeAgent().run({"raw_input": raw_input})
    if not isinstance(result.data, RfqIntakeOut):  # pragma: no cover - schema is fixed
        raise AgentServiceError("intake agent returned the wrong schema")
    return result


@transaction.atomic
def analyse_quote(quote: Quote) -> AgentResult | None:
    """Attach A5's read to ``quote.analysis``. Advice beside the price.

    Returns ``None`` for a quote that is not in Hong Kong dollars: every figure
    in the schema is HKD, the market percentiles are HKD, and converting would
    need an exchange rate the platform neither has nor should invent. Those
    quotes still appear in the comparison table - they just carry no analysis.

    The percentiles come from SQL and exclude this quote, so a quote is never
    measured against itself (AI_AGENTS A5).
    """
    from apps.rfq import selectors as rfq_selectors

    if quote.currency != "HKD":
        logger.info("skipping analysis for %s: quote is in %s", quote.pk, quote.currency)
        return None

    percentiles = {
        key: {
            "p10": _to_major(values.p10),
            "p50": _to_major(values.p50),
            "p90": _to_major(values.p90),
            "sample_size": values.sample_size,
        }
        for key, values in rfq_selectors.market_percentiles(exclude_quote=quote).items()
    }
    rfq = quote.rfq
    result = QuoteAnalysisAgent().run(
        {
            "object_id": str(quote.pk),
            "services_needed": list(rfq.services_needed),
            "needs_bank_account": rfq.needs_bank_account,
            "budget_min_hkd": _to_major(rfq.budget_min_minor) if rfq.currency == "HKD" else None,
            "budget_max_hkd": _to_major(rfq.budget_max_minor) if rfq.currency == "HKD" else None,
            "total_first_year_hkd": _to_major(quote.first_year_total_minor),
            "total_renewal_hkd": _to_major(quote.renewal_total_minor),
            "includes_govt_fee": quote.includes_govt_fee,
            "validity_days": quote.validity_days,
            "delivery_days": quote.delivery_days,
            "message": quote.message,
            "line_items": [
                {
                    "label": item.label,
                    "source_label": item.custom_label or item.display_label,
                    "amount_hkd": _to_major(item.amount_minor),
                    "unit": item.unit,
                    "is_optional": item.is_optional,
                    "note": item.note,
                }
                for item in quote.line_items.all()
            ],
            "percentiles": percentiles,
        }
    )
    data = result.data
    if not isinstance(data, QuoteAnalysisOut):  # pragma: no cover - schema is fixed
        raise AgentServiceError("quote analysis agent returned the wrong schema")

    quote.analysis = {
        **data.model_dump(mode="json"),
        "model": QuoteAnalysisAgent.model,
        "prompt_version": QuoteAnalysisAgent().prompt_version,
        "run_id": result.run_id,
        "used_fallback": result.used_fallback,
        "fallback_reason": result.fallback_reason,
        "percentile_sample": {key: values["sample_size"] for key, values in percentiles.items()},
    }
    quote.save(update_fields=["analysis", "updated_at"])
    return result


def _to_major(amount_minor: int | None) -> int | None:
    """Whole HKD from minor units, for the agent layer's ``_hkd`` fields.

    Truncating rather than rounding: the cents on a HKD 6,800.00 quote are
    always zero, and a figure that rounded up would be a price nobody quoted.
    """
    if amount_minor is None:
        return None
    return int(Money(amount_minor, "HKD").to_decimal())


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
