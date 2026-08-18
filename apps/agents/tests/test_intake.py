"""A1, the prefill agent.

Two things are worth testing about a prefill: what it reads, and what it
refuses to invent. The second one carries the weight - AI_AGENTS A1 sets the
hallucinated budget rate at zero, and a budget the buyer never wrote does not
stay on the buyer's screen. It goes to the wall, and licensed companies price
against it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from apps.agents.rfq_intake import MAX_INPUT_CHARS, RfqIntakeAgent

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.fixture
def agent() -> RfqIntakeAgent:
    return RfqIntakeAgent()


# ---------------------------------------------------------------- what it reads


def test_the_fallback_reads_the_services_a_buyer_named(agent: RfqIntakeAgent) -> None:
    out = agent.fallback(
        {"raw_input": "想在香港注册公司，需要注册地址和公司秘书，还要做账报税。"}, "disabled"
    )

    assert set(out.services_needed) == {
        "incorporation",
        "registered_address",
        "company_secretary",
        "accounting",
        "tax_filing",
    }


def test_asking_about_a_bank_implies_the_help_with_opening_one(agent: RfqIntakeAgent) -> None:
    """The buyer says "open an account"; the form has a service tag for it.
    Leaving that box empty makes the buyer find it themselves, which is the
    work the prefill exists to save."""
    out = agent.fallback({"raw_input": "注册好公司后想开个汇丰的公司账户。"}, "disabled")

    assert out.needs_bank_account is True
    assert "bank_account_assist" in out.services_needed
    assert out.preferred_bank_types == ["traditional"]


def test_the_more_specific_company_type_wins(agent: RfqIntakeAgent) -> None:
    """「分公司」 contains 「公司」 and 「有限公司」 contains itself; a table read in
    the wrong order turns a branch into a private limited company."""
    out = agent.fallback({"raw_input": "想在香港开一家代表处，不经营。"}, "disabled")

    assert out.company_type == "hk_rep_office"


def test_an_empty_paragraph_fills_in_nothing(agent: RfqIntakeAgent) -> None:
    out = agent.fallback({"raw_input": ""}, "disabled")

    assert out.services_needed == []
    assert out.company_type == "undecided"
    assert out.timeline == "undecided"


# ------------------------------------------------------------ what it will not


def test_a_figure_with_no_currency_is_not_a_budget(agent: RfqIntakeAgent) -> None:
    out = agent.fallback({"raw_input": "预算大概一万五，能做吗？"}, "disabled")

    assert out.budget_max_hkd is None
    assert "budget" in out.missing_fields


def test_a_renminbi_figure_is_never_read_as_hong_kong_dollars(agent: RfqIntakeAgent) -> None:
    """No exchange rate exists in this codebase, and a converted budget is a
    number the buyer never agreed to."""
    out = agent.fallback({"raw_input": "预算 人民币 50,000 元，注册公司。"}, "disabled")

    assert out.budget_max_hkd is None


def test_an_explicit_hong_kong_figure_is_a_ceiling_and_not_a_floor(agent: RfqIntakeAgent) -> None:
    out = agent.fallback({"raw_input": "预算 HK$12,000 以内。"}, "disabled")

    assert out.budget_max_hkd == 12_000
    assert out.budget_min_hkd is None


def test_ten_thousands_are_read_as_ten_thousands(agent: RfqIntakeAgent) -> None:
    out = agent.fallback({"raw_input": "预算 2 万港币左右。"}, "disabled")

    assert out.budget_max_hkd == 20_000


def test_the_fallback_never_writes_a_title_or_a_line_of_business(agent: RfqIntakeAgent) -> None:
    """Both are summaries, and a keyword list cannot write one. An invented
    title is what the whole wall shows for this requirement."""
    out = agent.fallback({"raw_input": "做电商的，要注册公司。"}, "disabled")

    assert out.title == ""
    assert out.business_nature == ""
    assert {"title", "business_nature"} <= set(out.missing_fields)


def test_the_fallback_says_it_is_a_guess(agent: RfqIntakeAgent) -> None:
    out = agent.fallback({"raw_input": "注册公司。"}, "disabled")

    assert out.confidence == 0.3
    assert out.clarifying_questions == []


# --------------------------------------------------------------- what it sends


def test_contact_details_never_reach_the_model(agent: RfqIntakeAgent) -> None:
    """Buyers write a WeChat id in the box that told them not to. COMPLIANCE
    section 4 has no exception for the agent that reads their own form."""
    prompt = agent.build_user_prompt(
        {"raw_input": "注册公司，微信 abc-12345，电话 91234567，邮箱 me@example.com"}
    )

    assert "91234567" not in prompt
    assert "me@example.com" not in prompt
    assert "[PHONE]" in prompt
    assert "[EMAIL]" in prompt


def test_a_very_long_paragraph_is_truncated(agent: RfqIntakeAgent) -> None:
    prompt = agent.build_user_prompt({"raw_input": "注册公司。" * 5000})

    assert len(prompt) < MAX_INPUT_CHARS + 200


@pytest.mark.django_db
def test_the_agent_falls_back_when_the_platform_switch_is_off(
    settings: Any, agent: RfqIntakeAgent
) -> None:
    settings.AGENTS_ENABLED = False

    result = agent.run({"raw_input": "注册公司加开户。"})

    assert result.used_fallback is True
    assert result.fallback_reason == "disabled"
    assert "incorporation" in result.data.services_needed  # type: ignore[union-attr]


@pytest.mark.django_db
def test_one_switch_can_be_turned_off_without_the_others(
    settings: Any, agent: RfqIntakeAgent, fake_client: Callable[..., Any]
) -> None:
    """CLAUDE.md's three-stage kill switch: this agent alone, off."""
    settings.AGENTS_ENABLED = True
    settings.AGENT_ENABLED_RFQ_INTAKE = False
    fake_client()

    result = agent.run({"raw_input": "注册公司。"})

    assert result.used_fallback is True


@pytest.mark.django_db
def test_the_run_is_recorded_even_when_nothing_was_called(
    settings: Any, agent: RfqIntakeAgent
) -> None:
    """CLAUDE.md rule 4. A fallback is still a decision the platform made."""
    from apps.agents.models import AgentRun

    settings.AGENTS_ENABLED = False

    agent.run({"raw_input": "注册公司。"})

    run = AgentRun.objects.get()
    assert run.agent_name == "rfq_intake"
    assert run.used_fallback is True
