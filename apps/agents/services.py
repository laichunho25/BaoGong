"""Writes. Every agent result reaches the database through this module.

CLAUDE.md rule 3 is the whole design here: nothing below changes a status, a
score, or a verification outcome. ``moderate_review`` writes to
``Review.moderation``; ``extract_nnc1`` writes to ``Nnc1Verification.extracted``.
Both of those columns exist precisely so that an agent has somewhere to put an
opinion that is not the same place a decision lives.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts import selectors as account_selectors
from apps.agents import advisor, matching, registry_diff, review_moderation
from apps.agents.advisor import AdvisorAgent
from apps.agents.matching import MatchingAgent
from apps.agents.models import AgentFeedback, AgentRun, FeedbackVerdict
from apps.agents.nnc1_extraction import Nnc1ExtractionAgent
from apps.agents.quote_analysis import QuoteAnalysisAgent
from apps.agents.registry_diff import RegistryDiffAgent
from apps.agents.review_moderation import ReviewModerationAgent
from apps.agents.rfq_intake import RfqIntakeAgent
from apps.agents.schemas import (
    AdvisorOut,
    DiffDigestOut,
    MatchingOut,
    ModerationOut,
    Nnc1Out,
    QuoteAnalysisOut,
    RfqIntakeOut,
)
from apps.core.money import Money
from apps.core.notifications import absolute_url, notify
from apps.providers import services as provider_services
from apps.providers.models import ClaimStatus, Provider
from apps.registry.models import ChangeSeverity, LicenseeChange, SyncRun

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.agents.base import AgentResult
    from apps.content.models import Article
    from apps.reviews.models import Nnc1Verification, Review
    from apps.rfq.models import Quote, Rfq

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
def match_providers(rfq: Rfq) -> AgentResult | None:
    """Attach A2's shortlist to ``rfq.matches``. Suggestions, nothing more.

    Returns ``None`` when SQL screened everybody out - an empty pool is a fact
    about the register, not something a model can improve, and calling one to
    rank nothing would spend money to produce an empty list.

    What this writes gives no company any standing on the requirement: the wall
    is unchanged, every claimed company may still quote, and no ordering here
    affects who sees what. It is a reading list for the buyer (CLAUDE.md rule 3,
    AI_AGENTS A2).
    """
    from apps.providers import selectors as provider_selectors

    candidates = provider_selectors.match_candidates(
        provider_selectors.CandidateFilters(
            services=tuple(rfq.services_needed),
            needs_bank_account=rfq.needs_bank_account,
            budget_max_minor=rfq.budget_max_minor if rfq.currency == "HKD" else None,
            currency=rfq.currency,
        )
    )
    if not candidates:
        logger.info("no candidates for rfq %s; nothing to rank", rfq.pk)
        return None

    wanted = list(rfq.services_needed)
    summaries = [candidate_summary(provider, services=wanted) for provider in candidates]
    agent = MatchingAgent()
    result = agent.run(
        {
            "object_id": str(rfq.pk),
            "services_needed": list(rfq.services_needed),
            "company_type": rfq.company_type,
            "business_nature": rfq.business_nature,
            "needs_bank_account": rfq.needs_bank_account,
            "budget_min_hkd": _to_major(rfq.budget_min_minor) if rfq.currency == "HKD" else None,
            "budget_max_hkd": _to_major(rfq.budget_max_minor) if rfq.currency == "HKD" else None,
            "timeline": rfq.timeline,
            "candidates": summaries,
        }
    )
    data = result.data
    if not isinstance(data, MatchingOut):  # pragma: no cover - schema is fixed
        raise AgentServiceError("matching agent returned the wrong schema")

    # Screened on both paths: an ungrounded reason is not more acceptable for
    # having come from a template (AI_AGENTS A2, grounding violation rate = 0).
    screened = matching.screen_matches(data, summaries)
    rfq.matches = {
        **screened.model_dump(mode="json"),
        "model": MatchingAgent.model,
        "prompt_version": agent.prompt_version,
        "run_id": result.run_id,
        "used_fallback": result.used_fallback,
        "fallback_reason": result.fallback_reason,
        "pool_size": len(summaries),
        "generated_at": timezone.now().isoformat(),
    }
    rfq.save(update_fields=["matches", "updated_at"])
    return result


def candidate_summary(provider: Provider, *, services: list[str]) -> dict[str, Any]:
    """One company as the flat facts A2 is allowed to cite.

    Everything here is published on the company's own directory page, so a
    reason built from it is a reason the buyer can check. ``tier`` is
    deliberately absent: it is partly a paid standing, and a model told which
    companies pay would have a fact it could offer as a reason to choose one
    (COMPLIANCE section 5 - commercial placement is disclosed and separate,
    never dressed up as fit).
    """
    offerings = [offering for offering in provider.offerings.all() if offering.is_active]
    prices = [
        amount
        for offering in offerings
        if not services or offering.category in services
        for price in offering.prices.all()
        if price.currency == "HKD"
        for amount in [
            price.min_amount_minor if price.min_amount_minor is not None else price.amount_minor
        ]
        if amount is not None
    ]
    cheapest = min(prices) if prices else None
    founded = provider.founded_year
    return {
        "provider_id": provider.slug,
        "name": provider.display_name,
        "district": provider.licensee.district if provider.licensee else "",
        "services": [offering.category for offering in offerings],
        "languages": list(provider.languages),
        "supports_simplified": provider.supports_simplified,
        "bank_account_support": provider.bank_account_support,
        "bank_types": list(provider.bank_types),
        "remote_onboarding": provider.remote_onboarding,
        "non_resident_shareholder_experience": provider.non_resident_shareholder_experience,
        "certified": any(cert.is_current for cert in provider.certifications.all()),
        "claimed": provider.claim_status == ClaimStatus.CLAIMED,
        "rating": float(provider.rating_cached) if provider.rating_cached is not None else None,
        "verified_review_count": provider.verified_review_count,
        "price_from_hkd": _to_major(cheapest),
        "years_active": (timezone.now().year - founded) if founded else None,
    }


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


@dataclass(frozen=True, slots=True)
class AdvisorAnswer:
    """A6's answer plus the articles it is made of.

    ``sources`` are the distinct articles behind the surviving citations, in
    the order they were cited, so the page can offer them as links. An answer
    without sources is a refusal by construction - see ``advisor.screen_answer``.
    """

    data: AdvisorOut
    sources: list[Article]
    used_fallback: bool
    run_id: str | None = None
    fallback_reason: str = ""


def answer_question(*, question: str) -> AdvisorAnswer:
    """Answer an education question out of the platform's own guides (A6).

    **Writes nothing but the ``AgentRun``.** Nothing here creates content, and
    an answer is not stored: it is one reader's question, and keeping it would
    mean keeping a record of what a person is trying to do with their money
    (COMPLIANCE section 4).

    Retrieval happens before the model and decides whether there is a model
    call at all. No passages means no answer this platform can stand behind, so
    it refuses without spending anything - the alternative is paying a vendor to
    tell a buyer something none of our articles say.
    """
    from apps.content import selectors as content_selectors

    chunks = list(content_selectors.search_chunks(question, limit=advisor.RETRIEVAL_LIMIT))
    passages = [
        {
            "article_slug": chunk.article.slug,
            "ordinal": chunk.ordinal,
            "heading": chunk.heading,
            "title": chunk.article.title,
            "text": chunk.text,
        }
        for chunk in chunks
    ]
    if not passages:
        return AdvisorAnswer(
            data=advisor.refusal([]), sources=[], used_fallback=True, fallback_reason="no_passages"
        )

    result = AdvisorAgent().run({"question": question, "passages": passages})
    data = result.data
    if not isinstance(data, AdvisorOut):  # pragma: no cover - schema is fixed
        raise AgentServiceError("advisor agent returned the wrong schema")

    screened = advisor.screen_answer(data, passages, question=question)
    by_slug = {chunk.article.slug: chunk.article for chunk in chunks}
    sources: list[Article] = []
    for citation in screened.citations:
        article = by_slug.get(citation.article_slug)
        if article is not None and article not in sources:
            sources.append(article)
    return AdvisorAnswer(
        data=screened,
        sources=sources,
        used_fallback=result.used_fallback,
        run_id=result.run_id,
        fallback_reason=result.fallback_reason,
    )


@dataclass(frozen=True, slots=True)
class RegistryDigest:
    """A7's digest, plus what the rules did before the model was asked.

    ``suspended`` is the part that changed the platform rather than described
    it, so it is on the result rather than only in the log: a caller that wants
    to know whether today's sync took a paying page off the shelf should not
    have to read an email to find out.
    """

    data: DiffDigestOut
    critical_count: int
    suspended: list[str]
    notified: int
    used_fallback: bool
    run_id: str | None = None
    fallback_reason: str = ""


def summarise_registry_diff(sync_run: SyncRun) -> RegistryDigest | None:
    """Read one sync run's differences and tell operations what happened (A7).

    The order here is the opposite of the obvious one, deliberately. Severity,
    the suspension and the decision to mail anybody are all settled by rules
    **before** the agent is called, so that a day when the model is unreachable
    is still a day when operations is told and a delisted paying company is
    still off the shelf. The model contributes the wording of a mail that would
    have gone out either way.

    ``None`` for a run with no differences: a digest saying nothing happened
    trains people to stop reading digests.
    """
    changes = list(LicenseeChange.objects.filter(sync_run=sync_run).order_by("licence_no"))
    if not changes:
        return None

    rows = _diff_rows(changes)
    _store_severities(changes, rows)
    suspended = _suspend_delisted_paid_pages(rows)

    result = RegistryDiffAgent().run(
        {
            "object_id": str(sync_run.pk),
            "sync_date": timezone.localtime(sync_run.started_at).strftime("%Y-%m-%d"),
            "row_count": sync_run.row_count,
            "prev_row_count": sync_run.prev_row_count,
            "rows": rows,
        }
    )
    data = result.data
    if not isinstance(data, DiffDigestOut):  # pragma: no cover - schema is fixed
        raise AgentServiceError("registry diff agent returned the wrong schema")

    screened = registry_diff.screen_digest(data, rows)
    _store_summaries(changes, screened)
    notified = _tell_operations(sync_run, screened)

    return RegistryDigest(
        data=screened,
        critical_count=len(screened.critical_items),
        suspended=suspended,
        notified=notified,
        used_fallback=result.used_fallback,
        run_id=result.run_id,
        fallback_reason=result.fallback_reason,
    )


def _diff_rows(changes: list[LicenseeChange]) -> list[dict[str, Any]]:
    """One dict per change, carrying the platform facts severity depends on.

    The provider rows are fetched in one query rather than per change: a sync
    that renames two thousand companies would otherwise do two thousand
    queries inside a Celery task with a time limit on it.
    """
    licence_nos = {change.licence_no for change in changes}
    providers = {
        provider.licensee.licence_no: provider
        for provider in Provider.objects.select_related("licensee").filter(
            licensee__licence_no__in=licence_nos
        )
        if provider.licensee is not None
    }

    rows: list[dict[str, Any]] = []
    for change in changes:
        provider = providers.get(change.licence_no)
        claimed = provider is not None and provider.claim_status == ClaimStatus.CLAIMED
        paid = (
            claimed
            and provider is not None
            and provider.tier in provider_services.PAID_TIERS
            and provider.paid_placement_suspended_at is None
        )
        rows.append(
            {
                "licence_no": change.licence_no,
                "change_type": change.change_type,
                "before": change.before,
                "after": change.after,
                "claimed": claimed,
                "paid": paid,
                "provider_name": provider.display_name if provider is not None else "",
                "provider_id": str(provider.pk) if provider is not None else "",
                "severity": registry_diff.severity_for(
                    change_type=change.change_type, claimed=claimed, paid=paid
                ),
            }
        )
    return rows


def _store_severities(changes: list[LicenseeChange], rows: list[dict[str, Any]]) -> None:
    """Write the rule-decided severity back onto the diff rows.

    The sync pipeline sets a first severity knowing nothing about providers -
    it lives in ``apps.registry`` and rule 1 keeps it there. This is the same
    decision made once the platform side is known, and it is the one the digest
    and the admin queue are read against.
    """
    changed = []
    for change, row in zip(changes, rows, strict=True):
        if change.severity != row["severity"]:
            change.severity = row["severity"]
            changed.append(change)
    LicenseeChange.objects.bulk_update(changed, ["severity"], batch_size=500)


def _suspend_delisted_paid_pages(rows: list[dict[str, Any]]) -> list[str]:
    """Stop promoting any paying company whose licence left the register.

    AI_AGENTS A7 allows this one automation because it can only ever remove
    promotion. Nothing is unpublished, nothing is deleted, and nothing is said
    to a buyer that a human did not write - the page keeps standing and keeps
    carrying the deregistration notice.
    """
    suspended: list[str] = []
    for row in registry_diff.critical_rows(rows):
        provider = Provider.objects.filter(pk=row["provider_id"]).first()
        if provider is not None and provider_services.suspend_paid_placement(provider):
            suspended.append(str(row["licence_no"]))
    return suspended


def _store_summaries(changes: list[LicenseeChange], digest: DiffDigestOut) -> None:
    """Attach the agent's sentence to the change it is about.

    ``ai_summary`` is an annotation beside the row in the admin queue and
    nothing reads it as a fact (CLAUDE.md rule 3) - which is also why only the
    critical rows get one. A generated sentence on every routine rename would
    be thousands of sentences a year nobody asked for.
    """
    by_licence = {item.licence_no: item for item in digest.critical_items}
    annotated = []
    for change in changes:
        item = by_licence.get(change.licence_no)
        if item is None or change.severity != ChangeSeverity.CRITICAL:
            continue
        change.ai_summary = " ".join(
            part for part in (item.what, item.why_it_matters, item.action) if part
        )
        annotated.append(change)
    LicenseeChange.objects.bulk_update(annotated, ["ai_summary"], batch_size=500)


def _tell_operations(sync_run: SyncRun, digest: DiffDigestOut) -> int:
    """Mail the digest to the moderators, but only when there is something to do.

    Returns how many changes were marked as notified. The mark is what stops a
    retried task from sending a second copy of an alert somebody has already
    acted on.
    """
    if not digest.critical_items:
        return 0

    licence_nos = [item.licence_no for item in digest.critical_items]
    unsent = LicenseeChange.objects.filter(
        sync_run=sync_run, licence_no__in=licence_nos, notified_at__isnull=True
    )
    marked = unsent.update(notified_at=timezone.now())
    if not marked:
        return 0

    notify(
        template="registry_digest",
        recipients=[user.email for user in account_selectors.moderators()],
        context={
            "headline": digest.headline,
            "routine_summary": digest.routine_summary,
            "counts": digest.counts,
            "items": [item.model_dump(mode="json") for item in digest.critical_items],
            "sync_date": timezone.localtime(sync_run.started_at).strftime("%Y-%m-%d"),
            "url": absolute_url(reverse("admin:registry_licenseechange_changelist")),
        },
    )
    return marked


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
