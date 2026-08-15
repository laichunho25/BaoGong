"""Structured output schemas.

CLAUDE.md section 5: every agent answers through a tool call whose input schema
is one of these models. Nothing parses free text - a regex over prose is a
parser that fails silently the first time the model rephrases itself, and by
then it has written something wrong into a moderation queue.

Pydantic rather than hand-written JSON Schema because the same model then does
the validation on the way back in. ``tool_schema`` below is what goes to the
API; ``model_validate`` is what decides whether the answer is usable.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: AI_AGENTS A4. Kept as a Literal so an invented label is a schema failure
#: rather than a new label quietly appearing in the moderation queue.
ModerationLabel = Literal[
    "defamation_risk",
    "unsubstantiated_claim",
    "personal_data_leak",
    "spam_or_ad",
    "competitor_attack",
    "off_topic",
    "profanity",
    "guarantees_bank_success",
    "looks_like_pr_copy",
    "non_specific",
]

#: Labels that send a review to a human no matter what the model recommends
#: (AI_AGENTS A4, COMPLIANCE section 3). Defamation and leaked personal data
#: are the two mistakes that cannot be undone by hiding the review afterwards.
ESCALATING_LABELS = frozenset({"defamation_risk", "personal_data_leak"})


class StrictSchema(BaseModel):
    """Reject unknown keys.

    A model that adds a field is a model that has drifted from the prompt, and
    silently dropping the extra key would hide exactly the signal that says the
    prompt needs a new version.
    """

    model_config = ConfigDict(extra="forbid")


class ModerationOut(StrictSchema):
    """A4's read of one review. Advice, in every field."""

    labels: list[ModerationLabel] = Field(default_factory=list)
    severity: Literal["none", "low", "medium", "high"] = "none"
    reasons: list[str] = Field(default_factory=list, max_length=5)
    #: Verbatim fragments of the review that should be masked before publishing
    #: - phone numbers, third-party names. Applied by a human, never in place.
    suggested_redactions: list[str] = Field(default_factory=list, max_length=20)
    recommended_action: Literal["publish", "human_review", "reject"] = "human_review"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Nnc1Out(StrictSchema):
    """A3's read of one NNC1.

    ``document_looks_authentic`` is the field most likely to be misread by
    whoever wires this up next: a ``False`` may only send the case to a human.
    Nothing in this schema may fail a verification on its own (AI_AGENTS A3).
    """

    company_name_en: str | None = None
    company_name_zh: str | None = None
    company_number: str | None = None
    #: ISO date as a string: a malformed date should be a value a moderator can
    #: see and judge, not a validation error that discards the whole reading.
    incorporation_date: str | None = None
    secretary_name: str | None = None
    secretary_licence_no: str | None = None
    secretary_is_corporate: bool = False
    document_looks_authentic: bool = True
    quality_issues: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


def tool_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """The JSON Schema the Anthropic tool definition carries.

    ``$defs``/``$ref`` are left as pydantic emits them; the API accepts them,
    and rewriting them here would be a second schema implementation to keep in
    step with the first.
    """
    return schema.model_json_schema()
