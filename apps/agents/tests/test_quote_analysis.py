"""A5, the quote reader.

The rules underneath it are the part that has to be right, because they run
whenever the model does not: with the API switched off, "this quote does not
price the government fee" is still arithmetic on a closed list of labels, and
that sentence is most of what the comparison table is for.

The other subject here is the prompt. AI_AGENTS A5 puts the market percentiles
in SQL for one reason - a model asked what a Hong Kong incorporation costs will
answer from nothing - so the tests check that the figures the model sees came
from the caller, and that their absence is stated rather than left blank.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.agents.quote_analysis import (
    MIN_USEFUL_VALIDITY_DAYS,
    QuoteAnalysisAgent,
    below_market_p10,
    completeness,
    expected_items,
    missing_govt_fee,
)


@pytest.fixture
def agent() -> QuoteAnalysisAgent:
    return QuoteAnalysisAgent()


@pytest.fixture
def ctx() -> dict[str, Any]:
    """A plain incorporation quote that prices neither statutory charge."""
    return {
        "services_needed": ["incorporation", "company_secretary"],
        "needs_bank_account": False,
        "budget_min_hkd": None,
        "budget_max_hkd": None,
        "total_first_year_hkd": 8_000,
        "total_renewal_hkd": 4_000,
        "includes_govt_fee": False,
        "validity_days": 30,
        "delivery_days": 5,
        "message": "",
        "line_items": [
            {
                "label": "incorporation_service",
                "source_label": "注册服务费",
                "amount_hkd": 5_000,
                "is_optional": False,
            },
            {
                "label": "company_secretary",
                "source_label": "秘书首年",
                "amount_hkd": 3_000,
                "is_optional": False,
            },
        ],
        "percentiles": {},
    }


# --------------------------------------------------------------------- the rules


def test_a_quote_that_prices_neither_statutory_charge_is_flagged() -> None:
    assert missing_govt_fee(priced={"incorporation_service"}, includes_govt_fee=False) is True


def test_a_company_that_said_the_total_includes_them_is_not_flagged() -> None:
    """It accounted for the fee without itemising it. Flagging that anyway
    trains buyers to scroll past the flag that matters."""
    assert missing_govt_fee(priced={"incorporation_service"}, includes_govt_fee=True) is False


def test_pricing_either_statutory_charge_clears_the_flag() -> None:
    assert missing_govt_fee(priced={"business_registration_fee"}, includes_govt_fee=False) is False


def test_no_market_figure_is_not_the_same_as_cheap() -> None:
    """The selector refuses to publish a percentile from a small sample, so an
    empty dict arrives here meaning "we do not know" - which must never be
    rendered to a buyer as "below market"."""
    assert below_market_p10(total=1_000, percentiles={}) is False


def test_a_total_under_the_tenth_percentile_is_flagged() -> None:
    percentiles = {"first_year_total": {"p10": 6_800, "p50": 9_500, "p90": 15_000}}

    assert below_market_p10(total=5_000, percentiles=percentiles) is True
    assert below_market_p10(total=6_800, percentiles=percentiles) is False


def test_what_is_expected_depends_on_what_the_buyer_asked_for() -> None:
    """A quote for bookkeeping is not incomplete for having no company kit."""
    assert expected_items(["accounting"]) == ["accounting"]
    assert "company_kit" in expected_items(["incorporation"])


def test_a_request_naming_no_service_is_measured_against_everything() -> None:
    assert len(expected_items([])) > 1


def test_completeness_is_the_share_of_expected_items_priced() -> None:
    assert completeness(priced={"accounting"}, expected=["accounting", "audit_liaison"]) == 0.5
    assert completeness(priced=set(), expected=[]) == 1.0


# ------------------------------------------------------------------ the fallback


def test_the_fallback_names_the_missing_statutory_charges(
    agent: QuoteAnalysisAgent, ctx: dict[str, Any]
) -> None:
    out = agent.fallback(ctx, "disabled")

    assert "missing_govt_fee" in out.flags
    assert "govt_incorporation_fee" in out.missing_common_items
    assert out.confidence == 0.3


def test_the_fallback_keeps_only_flags_a_rule_can_decide(
    agent: QuoteAnalysisAgent, ctx: dict[str, Any]
) -> None:
    """Whether a scope is vague is a reading of the wording, and nobody read
    the wording on this path."""
    out = agent.fallback(ctx, "disabled")

    assert "vague_scope" not in out.flags


def test_a_quote_that_lapses_before_others_arrive_is_flagged(
    agent: QuoteAnalysisAgent, ctx: dict[str, Any]
) -> None:
    ctx["validity_days"] = MIN_USEFUL_VALIDITY_DAYS - 1

    out = agent.fallback(ctx, "disabled")

    assert "short_validity" in out.flags


def test_a_quote_with_no_renewal_figure_carries_that_as_a_risk(
    agent: QuoteAnalysisAgent, ctx: dict[str, Any]
) -> None:
    """Year two is where a cheap first year is recovered, and a quote silent
    about it is not comparable with one that is not."""
    ctx["total_renewal_hkd"] = None

    out = agent.fallback(ctx, "disabled")

    assert out.total_renewal_hkd is None
    assert out.hidden_fee_risks


def test_the_fallback_copies_the_total_rather_than_recomputing_it(
    agent: QuoteAnalysisAgent, ctx: dict[str, Any]
) -> None:
    """The line items here add to 8,000 and so does the total, but a quote
    where they disagree is the company's own arithmetic, and the platform is
    not entitled to correct a price."""
    ctx["total_first_year_hkd"] = 9_500

    out = agent.fallback(ctx, "disabled")

    assert out.total_first_year_hkd == 9_500


def test_the_fallback_asks_rather_than_concludes(
    agent: QuoteAnalysisAgent, ctx: dict[str, Any]
) -> None:
    """COMPLIANCE section 2: nothing the platform generates says anything
    about the company."""
    out = agent.fallback(ctx, "disabled")

    assert out.buyer_questions
    assert all(question.endswith("？") for question in out.buyer_questions)


# -------------------------------------------------------------------- the prompt


def test_the_prompt_carries_the_percentiles_it_was_given(
    agent: QuoteAnalysisAgent, ctx: dict[str, Any]
) -> None:
    ctx["percentiles"] = {
        "first_year_total": {"p10": 6_800, "p50": 9_500, "p90": 15_000, "sample_size": 24}
    }

    prompt = agent.build_user_prompt(ctx)

    assert "6800" in prompt
    assert "24 quotes" in prompt


def test_an_empty_market_is_said_out_loud(agent: QuoteAnalysisAgent, ctx: dict[str, Any]) -> None:
    """A missing section is a section a model fills in from memory."""
    prompt = agent.build_user_prompt(ctx)

    assert "Not enough comparable quotes" in prompt


def test_the_prompt_names_no_one(agent: QuoteAnalysisAgent, ctx: dict[str, Any]) -> None:
    """A quote is a document; reading it needs neither the buyer's name nor the
    name of whoever at the company sent it (COMPLIANCE section 4)."""
    prompt = agent.build_user_prompt(ctx)

    assert "provider" not in prompt.lower()
    assert "buyer@" not in prompt


def test_a_quote_with_no_line_items_says_so(agent: QuoteAnalysisAgent, ctx: dict[str, Any]) -> None:
    ctx["line_items"] = []

    prompt = agent.build_user_prompt(ctx)

    assert "the company gave a total only" in prompt


@pytest.mark.django_db
def test_the_agent_falls_back_when_the_platform_switch_is_off(
    settings: Any, agent: QuoteAnalysisAgent, ctx: dict[str, Any]
) -> None:
    settings.AGENTS_ENABLED = False

    result = agent.run(ctx)

    assert result.used_fallback is True
    assert "missing_govt_fee" in result.data.flags  # type: ignore[union-attr]
