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


#: The platform's own enums, restated as Literals so the model can only answer
#: in values the database already accepts. They are copies - pydantic needs
#: them at import time and importing a Django model into this module would tie
#: the schema layer to the app registry - so ``tests/test_schemas.py`` asserts
#: each one against its ``TextChoices`` and fails the day they drift apart.
ServiceCode = Literal[
    "incorporation",
    "company_secretary",
    "registered_address",
    "accounting",
    "audit_liaison",
    "bank_account_assist",
    "tax_filing",
    "trademark",
    "work_visa",
]
CompanyTypeCode = Literal[
    "hk_private_limited",
    "hk_branch",
    "hk_rep_office",
    "offshore",
    "undecided",
]
TimelineCode = Literal["asap", "within_1_month", "within_3_months", "flexible", "undecided"]
BankTypeCode = Literal["traditional", "virtual", "emi"]

#: AI_AGENTS A5. The standard comparison basis, minus ``other``: an item one
#: company invented is not a gap in anybody else's quote.
LineItemCode = Literal[
    "govt_incorporation_fee",
    "business_registration_fee",
    "incorporation_service",
    "company_secretary",
    "registered_address",
    "company_kit",
    "bank_account_assist",
    "annual_return",
    "accounting",
    "audit_liaison",
    "courier",
    "other",
]

#: A5's flags. Every one of them is a question for the buyer to ask, never a
#: verdict about the company - the front end wording rule is in AI_AGENTS A5
#: and in COMPLIANCE section 2.
QuoteFlag = Literal["below_market_p10", "missing_govt_fee", "vague_scope", "short_validity"]


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


class RfqIntakeOut(StrictSchema):
    """A1's reading of what a buyer wrote, as a form to be corrected.

    Every field here ends up in an input box the buyer looks at before anything
    is published (AI_AGENTS A1, CLAUDE.md rule 3), which is why the codes are
    the platform's own rather than the shorter ones in the spec: a prefill that
    needs a translation table between two enums is a prefill whose mistakes
    happen in the translation.

    ``missing_fields`` is what the model could not find, not what it guessed.
    A budget the buyer never mentioned must come back as ``None`` and appear
    here - the eval threshold for a hallucinated budget is zero.
    """

    title: str = Field(default="", max_length=140)
    company_type: CompanyTypeCode = "undecided"
    #: ISO 3166-1 alpha-2, upper case. Validated on the way into the form.
    shareholder_nationalities: list[str] = Field(default_factory=list, max_length=10)
    business_nature: str = Field(default="", max_length=200)
    services_needed: list[ServiceCode] = Field(default_factory=list)
    needs_bank_account: bool = False
    preferred_bank_types: list[BankTypeCode] = Field(default_factory=list)
    #: Major units, HKD. The form asks for major units too, so nothing here has
    #: to know about minor units - that conversion happens once, in the service.
    budget_min_hkd: int | None = Field(default=None, ge=0)
    budget_max_hkd: int | None = Field(default=None, ge=0)
    timeline: TimelineCode = "undecided"
    missing_fields: list[str] = Field(default_factory=list, max_length=12)
    #: At most three, in Simplified Chinese: shown under the prefilled form as
    #: "we could not tell, please check".
    clarifying_questions: list[str] = Field(default_factory=list, max_length=3)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class NormalizedItem(StrictSchema):
    """One priced part of a quote, mapped onto the standard comparison basis."""

    label: LineItemCode
    #: What the company called it, kept so a buyer can see what was mapped onto
    #: what. A normalisation nobody can check is a normalisation nobody should
    #: trust.
    source_label: str = Field(default="", max_length=120)
    amount_hkd: int | None = Field(default=None, ge=0)
    is_optional: bool = False


class HiddenFeeRisk(StrictSchema):
    """A cost the buyer may meet later that this quote does not price."""

    item: str = Field(max_length=120)
    why: str = Field(max_length=300)
    est_amount_hkd: int | None = Field(default=None, ge=0)


class QuoteAnalysisOut(StrictSchema):
    """A5's read of one quote. Questions to ask, never a verdict.

    ``flags`` name what the quote does not say; they never say anything about
    the company. The market percentile behind ``below_market_p10`` is computed
    in SQL by the platform and handed to the model - a model asked to estimate
    a market price will invent one (AI_AGENTS A5).
    """

    normalized_items: list[NormalizedItem] = Field(default_factory=list, max_length=30)
    missing_common_items: list[LineItemCode] = Field(default_factory=list)
    hidden_fee_risks: list[HiddenFeeRisk] = Field(default_factory=list, max_length=8)
    completeness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    total_first_year_hkd: int = Field(default=0, ge=0)
    total_renewal_hkd: int | None = Field(default=None, ge=0)
    flags: list[QuoteFlag] = Field(default_factory=list)
    #: Three at most, Simplified Chinese, phrased as questions for the company.
    buyer_questions: list[str] = Field(default_factory=list, max_length=3)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


def tool_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """The JSON Schema the Anthropic tool definition carries.

    ``$defs``/``$ref`` are left as pydantic emits them; the API accepts them,
    and rewriting them here would be a second schema implementation to keep in
    step with the first.
    """
    return schema.model_json_schema()
