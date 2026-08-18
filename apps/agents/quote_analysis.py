"""A5 - read one quote and tell the buyer what it does not say.

AI_AGENTS A5. The design constraint that shapes everything here is in the spec's
one bold sentence: **the market percentiles are computed in SQL, not by the
model.** ``rfq.selectors.market_percentiles`` runs ``PERCENTILE_CONT`` over live
quotes and the numbers are handed to the model in the prompt. A model asked what
a Hong Kong incorporation costs will answer, fluently, from nothing.

Two departures from the spec, both recorded because they change what a caller
gets back:

1. **Only HKD quotes are analysed** (``services.analyse_quote`` returns ``None``
   for the others). Every figure in ``QuoteAnalysisOut`` is named ``_hkd``, and
   comparing a CNY quote against HKD percentiles needs an exchange rate this
   platform does not have and has no business inventing.
2. **``total_renewal_hkd`` is nullable.** For one-off work there is nothing to
   renew, and a zero there reads as "renewal is free" in the comparison table -
   the same reason ``Quote.renewal_total_minor`` is nullable.

The flags are the part with a compliance edge on it. Each one names something
absent or ambiguous *in a document*; none of them says anything about the
company that wrote it, and the templates render them in that register
(「此报价未列明政府规费，建议向服务商确认」, never 「此公司不可信」 -
AI_AGENTS A5, COMPLIANCE section 2). The fallback keeps only the flags a rule
can decide, which is three of the four: whether a scope is vague is a reading,
not a check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Final

from django.utils.translation import gettext

from apps.agents.base import BaseAgent
from apps.agents.schemas import HiddenFeeRisk, NormalizedItem, QuoteAnalysisOut

if TYPE_CHECKING:
    from pydantic import BaseModel

#: The two statutory charges. A quote pricing neither of them, and not saying
#: its total includes them, is not comparable with one that does.
GOVT_FEE_LABELS: Final[frozenset[str]] = frozenset(
    {"govt_incorporation_fee", "business_registration_fee"}
)

#: Under this many days a buyer cannot realistically collect and compare other
#: quotes before this one lapses.
MIN_USEFUL_VALIDITY_DAYS = 7

#: What each requested service is normally priced in parts of. Used to ask
#: "what is missing" against what the buyer actually asked for: a quote for
#: accounting only is not incomplete for having no company kit.
SERVICE_LINE_ITEMS: Final[dict[str, tuple[str, ...]]] = {
    "incorporation": (
        "govt_incorporation_fee",
        "business_registration_fee",
        "incorporation_service",
        "company_kit",
    ),
    "company_secretary": ("company_secretary", "annual_return"),
    "registered_address": ("registered_address",),
    "accounting": ("accounting",),
    "audit_liaison": ("audit_liaison",),
    "tax_filing": ("accounting",),
    "bank_account_assist": ("bank_account_assist",),
}

#: Fallback for a request whose services map to nothing above - price the lot
#: rather than price nothing.
_ALL_EXPECTED: Final[tuple[str, ...]] = tuple(
    dict.fromkeys(label for labels in SERVICE_LINE_ITEMS.values() for label in labels)
)


class QuoteAnalysisAgent(BaseAgent):
    name: ClassVar[str] = "quote_analysis"
    model: ClassVar[str] = "claude-sonnet-5"
    prompt_file: ClassVar[str] = "quote_analysis_v1.md"
    output_schema: ClassVar[type[BaseModel]] = QuoteAnalysisOut
    max_tokens: ClassVar[int] = 2048
    object_type: ClassVar[str] = "rfq.Quote"

    def build_user_prompt(self, ctx: dict[str, Any]) -> str:
        """The quote, the requirement, and the percentiles - in that order.

        No buyer and no company member appears here. A quote is a document and
        the analysis is about the document; the identities on either side of it
        are not needed to read it (COMPLIANCE section 4).
        """
        lines = [
            "--- what the buyer asked for ---",
            f"Services requested: {', '.join(ctx.get('services_needed', [])) or 'not stated'}",
            f"Bank account needed: {bool(ctx.get('needs_bank_account'))}",
            f"Budget (HKD): {_range(ctx.get('budget_min_hkd'), ctx.get('budget_max_hkd'))}",
            "",
            "--- the quote ---",
            f"First-year total (HKD): {ctx.get('total_first_year_hkd', 0)}",
            f"Renewal total (HKD): {ctx.get('total_renewal_hkd') or 'not stated'}",
            f"Company ticked 'total includes government fees': "
            f"{bool(ctx.get('includes_govt_fee'))}",
            f"Valid for (days): {ctx.get('validity_days', 0)}",
            f"Delivery (working days): {ctx.get('delivery_days') or 'not stated'}",
            "",
            "Line items:",
        ]
        items = ctx.get("line_items", [])
        if not items:
            lines.append("(none - the company gave a total only)")
        for item in items:
            optional = " [optional]" if item.get("is_optional") else ""
            note = f" note: {item['note']}" if item.get("note") else ""
            lines.append(
                f'- {item.get("label")} "{item.get("source_label", "")}": '
                f"HKD {item.get('amount_hkd')} per {item.get('unit', 'one_off')}"
                f"{optional}{note}"
            )
        lines += ["", "Message from the company:", str(ctx.get("message") or "(none)")]

        lines += ["", "--- market prices on this platform (computed in SQL, use no others) ---"]
        percentiles = ctx.get("percentiles", {})
        if not percentiles:
            # Said plainly rather than left out: an absent section is a thing a
            # model fills in from memory.
            lines.append(
                "Not enough comparable quotes yet. You have no market figures, "
                "so do not use below_market_p10 and do not estimate any."
            )
        for key, values in percentiles.items():
            lines.append(
                f"- {key}: p10 HKD {values['p10']}, p50 HKD {values['p50']}, "
                f"p90 HKD {values['p90']} (from {values['sample_size']} quotes)"
            )
        return "\n".join(lines)

    def fallback(self, ctx: dict[str, Any], reason: str) -> QuoteAnalysisOut:
        """Set difference plus the three flags a rule can decide.

        This is the half of A5 that works with every model switched off, and it
        is most of the value: "this quote does not price the government fee" is
        arithmetic on a closed label list, not a judgement.
        """
        items = list(ctx.get("line_items", []))
        priced = {str(item.get("label")) for item in items}
        expected = expected_items(ctx.get("services_needed", []))
        missing = [label for label in expected if label not in priced]

        flags: list[Any] = []
        if missing_govt_fee(priced=priced, includes_govt_fee=bool(ctx.get("includes_govt_fee"))):
            flags.append("missing_govt_fee")
        if int(ctx.get("validity_days") or 0) < MIN_USEFUL_VALIDITY_DAYS:
            flags.append("short_validity")
        if below_market_p10(
            total=int(ctx.get("total_first_year_hkd") or 0),
            percentiles=ctx.get("percentiles", {}),
        ):
            flags.append("below_market_p10")

        risks: list[HiddenFeeRisk] = []
        if ctx.get("total_renewal_hkd") is None:
            risks.append(
                HiddenFeeRisk(
                    item=gettext("次年续期费用"),
                    why=gettext("报价没有列出续期费用，第二年起的开支无法从这份报价看出。"),
                    est_amount_hkd=None,
                )
            )

        return QuoteAnalysisOut(
            normalized_items=[
                NormalizedItem(
                    label=item.get("label"),
                    source_label=str(item.get("source_label", ""))[:120],
                    amount_hkd=item.get("amount_hkd"),
                    is_optional=bool(item.get("is_optional")),
                )
                for item in items
            ],
            missing_common_items=missing,  # type: ignore[arg-type]
            hidden_fee_risks=risks,
            completeness_score=completeness(priced=priced, expected=expected),
            total_first_year_hkd=int(ctx.get("total_first_year_hkd") or 0),
            total_renewal_hkd=ctx.get("total_renewal_hkd"),
            flags=flags,
            buyer_questions=buyer_questions(missing),
            # Keyword-free but still only arithmetic: nobody read the wording of
            # the quote, and the wording is where scope hides.
            confidence=0.3,
        )


def expected_items(services: list[str] | tuple[str, ...]) -> list[str]:
    """The standard items a quote for ``services`` is expected to price."""
    expected: list[str] = []
    for service in services:
        for label in SERVICE_LINE_ITEMS.get(str(service), ()):
            if label not in expected:
                expected.append(label)
    return expected or list(_ALL_EXPECTED)


def missing_govt_fee(*, priced: set[str], includes_govt_fee: bool) -> bool:
    """Whether the statutory charges are unaccounted for.

    A company that ticked "the total includes government fees" has accounted
    for them without itemising them, and flagging it anyway would train buyers
    to ignore the flag.
    """
    return not includes_govt_fee and not (GOVT_FEE_LABELS & priced)


def below_market_p10(*, total: int, percentiles: dict[str, Any]) -> bool:
    """Whether the first-year total sits under the platform's 10th percentile.

    False whenever there are no percentiles. The selector already refuses to
    publish one from a small sample, so "we have no market figure" and "this is
    not below market" arrive here as the same thing, and both must answer no.
    """
    from apps.rfq.selectors import FIRST_YEAR_TOTAL

    market = percentiles.get(FIRST_YEAR_TOTAL)
    if not market or not total:
        return False
    return total < int(market["p10"])


def completeness(*, priced: set[str], expected: list[str]) -> float:
    """Share of the expected items this quote actually prices, 0 to 1."""
    if not expected:
        return 1.0
    return round(len([label for label in expected if label in priced]) / len(expected), 2)


def buyer_questions(missing: list[str]) -> list[str]:
    """Up to three questions about what the quote left out.

    Written as questions to put to the company rather than as findings: the
    platform is not in a position to conclude anything about a price, and a
    buyer who asks gets an answer either way (COMPLIANCE section 6).
    """
    from apps.rfq.models import LineItemLabel

    labels = dict(LineItemLabel.choices)
    questions = [
        gettext("报价未列明「%(item)s」，请问是否已包含在总价内？")
        % {"item": str(labels.get(label, label))}
        for label in missing[:3]
    ]
    return questions


def _range(low: Any, high: Any) -> str:
    if low is None and high is None:
        return "not stated"
    floor = low if low is not None else "unspecified"
    ceiling = high if high is not None else "unspecified"
    return f"{floor} - {ceiling}"
