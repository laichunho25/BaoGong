"""A2's grounding layer and its fallback.

Whether the model picks a better order than SQL is the eval harness's question.
The question here is the one AI_AGENTS A2 puts a hard number on: a sentence
that claims something the company's own profile does not say must not be able
to reach the database, whichever path produced it.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.agents.matching import (
    MAX_MATCHES,
    MatchingAgent,
    candidate_facts,
    ground_reasons,
    screen_matches,
    template_concerns,
    template_reasons,
    unmatched_services,
)
from apps.agents.schemas import MatchingOut, MatchItem


def _candidate(**overrides: Any) -> dict[str, Any]:
    """A candidate summary shaped exactly as ``services.candidate_summary`` builds it."""
    candidate = {
        "provider_id": "example-ltd",
        "name": "Example Ltd",
        "district": "中西区",
        "services": ["incorporation", "company_secretary"],
        "languages": ["cantonese"],
        "supports_simplified": False,
        "bank_account_support": False,
        "bank_types": [],
        "remote_onboarding": False,
        "non_resident_shareholder_experience": False,
        "certified": False,
        "claimed": True,
        "rating": None,
        "verified_review_count": 0,
        "price_from_hkd": None,
        "years_active": None,
    }
    candidate.update(overrides)
    return candidate


@pytest.fixture
def agent() -> MatchingAgent:
    return MatchingAgent()


# --------------------------------------------------------------------- grounding


def test_a_reason_citing_a_fact_the_company_has_is_kept() -> None:
    kept = ground_reasons(["提供银行开户协助"], _candidate(bank_account_support=True))

    assert kept == ["提供银行开户协助"]


def test_a_reason_citing_a_fact_the_company_does_not_have_is_dropped() -> None:
    """The failure this module exists to prevent: an invented fact about a licensed company."""
    kept = ground_reasons(["可以协助银行开户"], _candidate(bank_account_support=False))

    assert kept == []


def test_a_reason_citing_nothing_checkable_is_dropped() -> None:
    """ "专业可靠" is not a fact the platform can stand behind about a named company."""
    kept = ground_reasons(["专业可靠，值得信赖"], _candidate())

    assert kept == []


def test_a_reason_promising_a_bank_outcome_is_dropped() -> None:
    """COMPLIANCE section 2. True or not, the platform does not say it."""
    kept = ground_reasons(["保证开户成功"], _candidate(bank_account_support=True))

    assert kept == []


def test_a_reason_naming_the_district_is_grounded_by_it() -> None:
    kept = ground_reasons(["位于中西区，见面方便"], _candidate(district="中西区"))

    assert kept == ["位于中西区，见面方便"]


def test_a_sentence_mixing_a_true_and_a_false_claim_is_dropped() -> None:
    """Half-true is not a category the buyer can act on, so it is not kept."""
    kept = ground_reasons(
        ["可远程办理，并提供简体中文服务"],
        _candidate(remote_onboarding=True, supports_simplified=False),
    )

    assert kept == []


def test_mandarin_in_the_language_list_supports_a_simplified_chinese_claim() -> None:
    kept = ground_reasons(
        ["提供普通话沟通"], _candidate(supports_simplified=False, languages=["mandarin"])
    )

    assert kept == ["提供普通话沟通"]


def test_the_review_claim_needs_a_verified_review() -> None:
    facts = candidate_facts(_candidate(verified_review_count=0))
    assert facts["has_reviews"] is False

    assert ground_reasons(["口碑不错"], _candidate(verified_review_count=0)) == []
    assert ground_reasons(["有已核验评价"], _candidate(verified_review_count=3)) != []


def test_a_concern_about_a_missing_price_is_grounded_by_the_absence() -> None:
    """A concern cites what is not there, so it is true when the fact is false."""
    kept = ground_reasons(
        ["平台上没有公开报价，价格需要向服务商确认"], _candidate(price_from_hkd=None)
    )

    assert len(kept) == 1


def test_saying_a_company_lacks_something_it_publishes_is_dropped() -> None:
    """The mirror-image fabrication, and just as damaging to the company."""
    kept = ground_reasons(["平台上没有公开报价"], _candidate(price_from_hkd=4800))

    assert kept == []


# ---------------------------------------------------------------------- screening


def test_a_company_that_was_never_a_candidate_is_dropped() -> None:
    """A model that remembered a name from somewhere else does not get to add it."""
    data = MatchingOut(
        items=[
            MatchItem(provider_id="invented-ltd", rank=1, fit_score=0.9),
            MatchItem(provider_id="example-ltd", rank=2, fit_score=0.8),
        ],
        confidence=0.8,
    )

    screened = screen_matches(data, [_candidate()])

    assert [item.provider_id for item in screened.items] == ["example-ltd"]


def test_ranks_are_renumbered_so_the_list_has_no_holes() -> None:
    data = MatchingOut(
        items=[
            MatchItem(provider_id="invented-ltd", rank=1),
            MatchItem(provider_id="example-ltd", rank=2),
            MatchItem(provider_id="other-ltd", rank=3),
        ]
    )

    screened = screen_matches(data, [_candidate(), _candidate(provider_id="other-ltd")])

    assert [item.rank for item in screened.items] == [1, 2]


def test_the_same_company_cannot_appear_twice() -> None:
    data = MatchingOut(
        items=[
            MatchItem(provider_id="example-ltd", rank=1),
            MatchItem(provider_id="example-ltd", rank=2),
        ]
    )

    screened = screen_matches(data, [_candidate()])

    assert len(screened.items) == 1


def test_screening_strips_an_ungrounded_reason_but_keeps_the_company() -> None:
    """The company did nothing wrong; the sentence did."""
    data = MatchingOut(
        items=[
            MatchItem(
                provider_id="example-ltd",
                rank=1,
                reasons=["提供银行开户协助", "位于中西区"],
            )
        ]
    )

    screened = screen_matches(data, [_candidate(bank_account_support=False, district="中西区")])

    assert len(screened.items) == 1
    assert screened.items[0].reasons == ["位于中西区"]


def test_screening_caps_the_list_at_what_a_buyer_will_read() -> None:
    candidates = [_candidate(provider_id=f"p{index}") for index in range(MAX_MATCHES + 5)]
    data = MatchingOut(
        items=[
            MatchItem(provider_id=candidate["provider_id"], rank=position)
            for position, candidate in enumerate(candidates, start=1)
        ][:MAX_MATCHES]
    )

    screened = screen_matches(data, candidates)

    assert len(screened.items) <= MAX_MATCHES


# ----------------------------------------------------------------------- fallback


def test_the_fallback_keeps_the_sql_order(agent: MatchingAgent) -> None:
    ctx = {
        "services_needed": ["incorporation"],
        "candidates": [_candidate(provider_id="first"), _candidate(provider_id="second")],
    }

    out = agent.fallback(ctx, "disabled")

    assert [item.provider_id for item in out.items] == ["first", "second"]
    assert [item.rank for item in out.items] == [1, 2]
    assert out.items[0].fit_score > out.items[1].fit_score


def test_the_fallback_says_it_did_not_read_the_requirement(agent: MatchingAgent) -> None:
    out = agent.fallback({"services_needed": [], "candidates": [_candidate()]}, "no_api_key")

    assert out.confidence <= 0.5


def test_every_fallback_sentence_survives_its_own_grounding(agent: MatchingAgent) -> None:
    """The templates are held to the rule the model is held to, not a softer one."""
    candidates = [
        _candidate(
            bank_account_support=True,
            remote_onboarding=True,
            supports_simplified=True,
            verified_review_count=4,
        )
    ]
    out = agent.fallback({"services_needed": ["incorporation"], "candidates": candidates}, "budget")

    screened = screen_matches(out, candidates)

    assert screened.items[0].reasons == out.items[0].reasons
    assert screened.items[0].concerns == out.items[0].concerns


def test_the_fallback_names_a_service_nobody_in_the_pool_offers() -> None:
    unmatched = unmatched_services([_candidate()], wanted=["incorporation", "work_visa"])

    assert len(unmatched) == 1


def test_a_missing_published_price_is_written_as_a_question_not_a_warning() -> None:
    concerns = template_concerns(_candidate(price_from_hkd=None), wanted=["incorporation"])

    assert any("报价" in concern for concern in concerns)


def test_reasons_stop_at_three() -> None:
    reasons = template_reasons(
        _candidate(
            bank_account_support=True,
            remote_onboarding=True,
            supports_simplified=True,
            verified_review_count=9,
        ),
        wanted=["incorporation"],
    )

    assert len(reasons) == 3


# ------------------------------------------------------------------- user prompt


def test_the_prompt_carries_the_facts_and_not_the_buyer(agent: MatchingAgent) -> None:
    prompt = agent.build_user_prompt(
        {
            "services_needed": ["incorporation"],
            "company_type": "limited",
            "business_nature": "trading",
            "needs_bank_account": True,
            "budget_max_hkd": 12000,
            "candidates": [_candidate(name="Example Ltd", buyer_email="buyer@example.com")],
        }
    )

    assert "example-ltd" in prompt
    assert "Example Ltd" in prompt
    assert "buyer@example.com" not in prompt


def test_the_prompt_never_shows_a_paid_standing(agent: MatchingAgent) -> None:
    """COMPLIANCE section 5. A model told who pays could offer it as a reason."""
    prompt = agent.build_user_prompt({"candidates": [_candidate()]})

    assert "tier" not in prompt
