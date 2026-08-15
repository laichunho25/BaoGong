"""A4 - risk classification for a newly submitted review.

AI_AGENTS A4 with one deliberate departure, recorded here because it is the
kind of thing that must not be discovered later by reading code: **this agent
cannot publish a review.** The doc allows auto-publish at ``severity=none`` and
``confidence >= 0.8`` for a verified user; CLAUDE.md rule 3 says AI output never
becomes fact on its own, and P4-1's ``publish_review`` requires a named
moderator and a written reason. Rule 3 wins. What A4 does instead is attach its
read to ``Review.moderation`` and, when the rules below say so, mark the review
as one a human must look at first.

The rule layer around the model is the part that actually decides:
``defamation_risk`` and ``personal_data_leak`` escalate no matter what the model
recommended, and so does ``severity=high``. A model that grows confident about
defamation is a model whose mistakes are lawsuits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from apps.agents.base import BaseAgent
from apps.agents.redaction import redact
from apps.agents.schemas import ESCALATING_LABELS, ModerationOut
from apps.core.compliance import Severity, check_banned_phrases

if TYPE_CHECKING:
    from pydantic import BaseModel

#: Below this many characters a review says nothing a buyer can use. Used by
#: the fallback only - the model judges specificity better than a length does.
_MIN_USEFUL_CHARS = 30


class ReviewModerationAgent(BaseAgent):
    name: ClassVar[str] = "review_moderation"
    model: ClassVar[str] = "claude-sonnet-5"
    prompt_file: ClassVar[str] = "moderation_v1.md"
    output_schema: ClassVar[type[BaseModel]] = ModerationOut
    max_tokens: ClassVar[int] = 1024
    object_type: ClassVar[str] = "reviews.Review"

    def build_user_prompt(self, ctx: dict[str, Any]) -> str:
        """The review text, redacted.

        Redacted even though ``personal_data_leak`` is one of the labels: the
        placeholders left behind by ``redact`` are enough for the model to see
        that contact details were present, and COMPLIANCE section 4 has no
        exception for this agent the way it does for NNC1 extraction.
        """
        body = redact(str(ctx.get("body", "")))
        services = ", ".join(ctx.get("services", [])) or "not stated"
        return (
            f"Company under review: {ctx.get('provider_name', 'unknown')}\n"
            f"Services the reviewer says they used: {services}\n"
            f"Overall score given: {ctx.get('overall', 'unknown')} out of 5\n"
            f"Reviewer's email is verified: {bool(ctx.get('author_verified'))}\n"
            f"\n--- review text ---\n{body}\n--- end ---"
        )

    def fallback(self, ctx: dict[str, Any], reason: str) -> ModerationOut:
        """Rules only: banned phrases, redaction hits, and length.

        Everything lands on ``human_review``. AI_AGENTS A4 says the fallback is
        the human queue, and the queue is the thing this platform can always
        afford - the review is not public while it waits.
        """
        body = str(ctx.get("body", ""))
        labels: list[Any] = []
        reasons: list[str] = []

        if redact(body) != body:
            labels.append("personal_data_leak")
            reasons.append("Contact details or an identity number appear in the text.")

        for violation in check_banned_phrases(body):
            if violation.code in {"guaranteed_bank_success", "bank_success_rate"}:
                labels.append("guarantees_bank_success")
                reasons.append("The text promises or quantifies a bank-account outcome.")
            elif violation.severity is Severity.BLOCKING:
                labels.append("unsubstantiated_claim")
                reasons.append(violation.explanation)

        if len(body.strip()) < _MIN_USEFUL_CHARS:
            labels.append("non_specific")
            reasons.append("Too short to describe an actual engagement.")

        # Deduplicate while keeping the order a moderator will read them in.
        labels = list(dict.fromkeys(labels))
        return ModerationOut(
            labels=labels,
            severity="medium" if labels else "none",
            reasons=reasons[:5],
            suggested_redactions=[],
            recommended_action="human_review",
            # Not a confidence in the classification so much as an instruction
            # not to lean on it: the rules see phrases, not meaning.
            confidence=0.3,
        )


def escalation_reason(data: ModerationOut, *, used_fallback: bool) -> str:
    """Why this review needs a person, in words a moderator can sort a queue by.

    Every review needs one today - A4 cannot publish (see the module
    docstring) - so this never returns empty. What it returns is *which*
    reason, which is what decides whether a case is looked at this morning or
    this week. Written as a function rather than a method so the queue, the
    tests and any future auto-publish switch consult one predicate.
    """
    if data.severity == "high":
        return "high_severity"
    escalating = sorted(ESCALATING_LABELS.intersection(data.labels))
    if escalating:
        return escalating[0]
    if used_fallback:
        return "no_agent_reading"
    return "routine"


#: The reasons that should be worked before the rest of the queue.
URGENT_REASONS = frozenset({"high_severity", *ESCALATING_LABELS})
