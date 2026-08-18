"""The golden set, and the run that uses it.

Split in two on purpose. Everything above the marked test is cheap, offline and
runs in CI, because a golden set that has quietly gone malformed is worse than
no golden set - it makes the eval pass. The marked test is the real thing: it
calls the API, costs money, and is excluded from CI (``-m 'not eval'``).
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from apps.agents.evals.runner import (
    ESCALATION_RECALL_THRESHOLD,
    INTAKE_SERVICES_F1_THRESHOLD,
    MATCHING_NDCG_THRESHOLD,
    MAX_FALSE_ESCALATION_RATE,
    MAX_GROUNDING_VIOLATION_RATE,
    MAX_HALLUCINATED_BUDGET_RATE,
    MISSING_GOVT_FEE_PRECISION_THRESHOLD,
    GoldenCase,
    IntakeCase,
    MatchingCase,
    QuoteCase,
    load_golden,
    load_intake_golden,
    load_matching_golden,
    load_quote_golden,
    ndcg_at_k,
    score,
    score_intake,
    score_matching,
    score_quote,
)
from apps.agents.matching import MatchingAgent, ground_reasons, screen_matches
from apps.agents.quote_analysis import QuoteAnalysisAgent
from apps.agents.review_moderation import URGENT_REASONS, ReviewModerationAgent, escalation_reason
from apps.agents.rfq_intake import RfqIntakeAgent
from apps.agents.schemas import ESCALATING_LABELS, ModerationLabel, ServiceCode

GOLDEN = load_golden("review_moderation")
INTAKE_GOLDEN = load_intake_golden()
QUOTE_GOLDEN = load_quote_golden()
MATCHING_GOLDEN = load_matching_golden()


def test_the_golden_set_is_large_enough() -> None:
    """AI_AGENTS principle 5: at least 20 rows."""
    assert len(GOLDEN) >= 20


def test_the_golden_set_holds_both_kinds_of_case() -> None:
    """A set of nothing but defamation would score 1.0 on a model that
    escalates everything, which is exactly the model this must not accept."""
    assert sum(1 for case in GOLDEN if case.expect_escalation) >= 4
    assert sum(1 for case in GOLDEN if not case.expect_escalation) >= 10


def test_every_expected_label_is_one_the_schema_allows() -> None:
    allowed = set(ModerationLabel.__args__)  # type: ignore[attr-defined]
    for case in GOLDEN:
        assert set(case.expect_labels) <= allowed, case.id


def test_cases_expecting_escalation_carry_an_escalating_label() -> None:
    """Otherwise the expectation is not derivable from the schema, and the
    eval is scoring against a rule that exists only in someone's head."""
    for case in GOLDEN:
        if case.expect_escalation:
            assert ESCALATING_LABELS.intersection(case.expect_labels), case.id


def test_ids_are_unique() -> None:
    assert len({case.id for case in GOLDEN}) == len(GOLDEN)


def test_scoring_counts_misses_and_false_alarms() -> None:
    cases = [
        GoldenCase("a", "x", True, ("defamation_risk",)),
        GoldenCase("b", "y", True, ("personal_data_leak",)),
        GoldenCase("c", "z", False, ()),
    ]

    result = score(cases, {"a": True, "b": False, "c": True})

    assert result.escalation_recall == 0.5
    assert result.false_escalation_rate == 1.0


def test_scoring_a_set_with_nothing_to_catch_does_not_divide_by_zero() -> None:
    assert score([GoldenCase("a", "x", False, ())], {}).escalation_recall == 1.0


def test_the_rules_alone_catch_the_leaks_in_the_golden_set() -> None:
    """The floor the platform has with the model switched off entirely.

    Not the full threshold - keyword rules cannot read an accusation - but
    every leaked phone number and email in the set must be caught, because
    those are the ones a regular expression genuinely can find.
    """
    agent = ReviewModerationAgent()
    leaks = [case for case in GOLDEN if "personal_data_leak" in case.expect_labels]

    for case in leaks:
        out = agent.fallback({"body": case.body}, "disabled")
        assert "personal_data_leak" in out.labels, case.id


@pytest.mark.eval
def test_review_moderation_against_the_real_api(settings: Any) -> None:
    """Run manually: ``uv run pytest -m eval``. Costs real money."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or key.startswith("test-"):
        pytest.skip("no real ANTHROPIC_API_KEY configured")
    settings.ANTHROPIC_API_KEY = key
    settings.AGENTS_ENABLED = True

    agent = ReviewModerationAgent()
    escalated: dict[str, bool] = {}
    for case in GOLDEN:
        result = agent.run({"object_id": case.id, "body": case.body})
        assert not result.used_fallback, f"{case.id} fell back: {result.fallback_reason}"
        reason = escalation_reason(result.data, used_fallback=False)  # type: ignore[arg-type]
        escalated[case.id] = reason in URGENT_REASONS

    result_score = score(GOLDEN, escalated)
    print(f"\nreview_moderation eval: {result_score}")
    assert result_score.escalation_recall >= ESCALATION_RECALL_THRESHOLD, str(result_score)
    assert result_score.false_escalation_rate <= MAX_FALSE_ESCALATION_RATE, str(result_score)


# --------------------------------------------------------------------- A1 intake


def test_the_intake_golden_set_is_large_enough() -> None:
    assert len(INTAKE_GOLDEN) >= 20
    assert len({case.id for case in INTAKE_GOLDEN}) == len(INTAKE_GOLDEN)


def test_every_expected_service_is_one_the_schema_allows() -> None:
    allowed = set(ServiceCode.__args__)  # type: ignore[attr-defined]
    for case in INTAKE_GOLDEN:
        assert set(case.expect_services) <= allowed, case.id


def test_the_intake_set_holds_cases_with_no_stated_budget() -> None:
    """Otherwise hallucinated_budget_rate is measured over nothing and passes
    by having nothing to fail on."""
    assert sum(1 for case in INTAKE_GOLDEN if not case.expect_budget) >= 10


def test_the_intake_set_holds_a_request_that_names_no_service() -> None:
    """A buyer who only says "I don't understand any of this" is the case the
    prefill is for, and the honest answer there is an empty form plus a
    question - not a guess at incorporation."""
    assert any(not case.expect_services for case in INTAKE_GOLDEN)


def test_intake_scoring_counts_both_halves_of_f1() -> None:
    cases = [
        IntakeCase("a", "x", ("incorporation", "accounting"), False),
        IntakeCase("b", "y", ("trademark",), True),
    ]

    result = score_intake(
        cases,
        # "a" finds one of two and invents one; "b" is right, and its budget
        # was stated so filling one in is not an invention.
        {"a": (("incorporation", "work_visa"), True), "b": (("trademark",), True)},
    )

    assert result.true_positives == 2
    assert result.false_positives == 1
    assert result.false_negatives == 1
    assert result.services_f1 == pytest.approx(2 / 3)
    assert result.hallucinated_budget_rate == 1.0


def test_intake_scoring_treats_a_missing_answer_as_finding_nothing() -> None:
    cases = [IntakeCase("a", "x", ("incorporation",), False)]

    result = score_intake(cases, {})

    assert result.recall == 0.0
    assert result.hallucinated_budget_rate == 0.0


def test_the_intake_fallback_never_invents_a_budget() -> None:
    """The floor with the model switched off. AI_AGENTS A1 puts
    hallucinated_budget_rate at zero, and a keyword matcher is just as capable
    of putting a number the buyer never wrote in front of licensed companies -
    including the RMB figures it must not silently convert.
    """
    agent = RfqIntakeAgent()

    for case in INTAKE_GOLDEN:
        if case.expect_budget:
            continue
        out = agent.fallback({"raw_input": case.raw_input}, "disabled")
        assert out.budget_min_hkd is None, case.id
        assert out.budget_max_hkd is None, case.id


def test_the_intake_fallback_flags_everything_it_did_not_fill() -> None:
    """A keyword match is a guess, and the form has to say which boxes are
    guesses (AI_AGENTS A1: all fields flagged needs_review)."""
    agent = RfqIntakeAgent()

    out = agent.fallback({"raw_input": "完全不懂，想问一下。"}, "disabled")

    assert out.confidence == 0.3
    assert "services_needed" in out.missing_fields


@pytest.mark.eval
def test_rfq_intake_against_the_real_api(settings: Any) -> None:
    """Run manually: ``uv run pytest -m eval``. Costs real money."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or key.startswith("test-"):
        pytest.skip("no real ANTHROPIC_API_KEY configured")
    settings.ANTHROPIC_API_KEY = key
    settings.AGENTS_ENABLED = True

    agent = RfqIntakeAgent()
    produced: dict[str, tuple[tuple[str, ...], bool]] = {}
    for case in INTAKE_GOLDEN:
        result = agent.run({"object_id": case.id, "raw_input": case.raw_input})
        assert not result.used_fallback, f"{case.id} fell back: {result.fallback_reason}"
        data = result.data
        has_budget = data.budget_min_hkd is not None or data.budget_max_hkd is not None  # type: ignore[union-attr]
        produced[case.id] = (tuple(data.services_needed), has_budget)  # type: ignore[union-attr]

    result_score = score_intake(INTAKE_GOLDEN, produced)
    print(f"\nrfq_intake eval: {result_score}")
    assert result_score.services_f1 >= INTAKE_SERVICES_F1_THRESHOLD, str(result_score)
    assert result_score.hallucinated_budget_rate <= MAX_HALLUCINATED_BUDGET_RATE, str(result_score)


# -------------------------------------------------------------- A5 quote analysis


def test_the_quote_golden_set_is_large_enough() -> None:
    assert len(QUOTE_GOLDEN) >= 20
    assert len({case.id for case in QUOTE_GOLDEN}) == len(QUOTE_GOLDEN)


def test_the_quote_set_holds_both_kinds_of_case() -> None:
    """Precision is only meaningful against quotes that must not be flagged."""
    assert sum(1 for case in QUOTE_GOLDEN if case.expect_missing_govt_fee) >= 5
    assert sum(1 for case in QUOTE_GOLDEN if not case.expect_missing_govt_fee) >= 10


def test_the_quote_set_holds_quotes_that_only_say_so_in_words() -> None:
    """The cases the rule cannot decide: no government line item, the checkbox
    not ticked, and the message saying in Chinese that the fees are included.
    Without these the eval scores a regular expression, not a reading.
    """
    subtle = [
        case
        for case in QUOTE_GOLDEN
        if not case.expect_missing_govt_fee
        and not case.quote["includes_govt_fee"]
        and not any(
            item["label"] in {"govt_incorporation_fee", "business_registration_fee"}
            for item in case.quote["line_items"]
        )
    ]
    assert len(subtle) >= 2


def test_quote_scoring_counts_misses_and_false_flags() -> None:
    cases = [
        QuoteCase("a", {}, True),
        QuoteCase("b", {}, True),
        QuoteCase("c", {}, False),
    ]

    result = score_quote(cases, {"a": True, "b": False, "c": True})

    assert result.recall == 0.5
    assert result.precision == 0.5


def test_quote_scoring_a_set_with_nothing_to_flag_does_not_divide_by_zero() -> None:
    assert score_quote([QuoteCase("a", {}, False)], {}).precision == 1.0
    assert score_quote([QuoteCase("a", {}, False)], {}).recall == 1.0


def test_the_quote_fallback_catches_every_itemised_missing_govt_fee() -> None:
    """The floor with the model switched off.

    Scored on recall, not precision: the rule cannot read 「已包含政府规费」 in
    a covering message, so it over-flags by design, and the two cases in the
    set that say it only in words are the ones the model is paid to get right.
    """
    agent = QuoteAnalysisAgent()
    should_flag = [case for case in QUOTE_GOLDEN if case.expect_missing_govt_fee]

    for case in should_flag:
        out = agent.fallback(case.quote, "disabled")
        assert "missing_govt_fee" in out.flags, case.id


@pytest.mark.eval
def test_quote_analysis_against_the_real_api(settings: Any) -> None:
    """Run manually: ``uv run pytest -m eval``. Costs real money."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or key.startswith("test-"):
        pytest.skip("no real ANTHROPIC_API_KEY configured")
    settings.ANTHROPIC_API_KEY = key
    settings.AGENTS_ENABLED = True

    agent = QuoteAnalysisAgent()
    flagged: dict[str, bool] = {}
    for case in QUOTE_GOLDEN:
        result = agent.run({"object_id": case.id, **case.quote})
        assert not result.used_fallback, f"{case.id} fell back: {result.fallback_reason}"
        flagged[case.id] = "missing_govt_fee" in result.data.flags  # type: ignore[union-attr]

    result_score = score_quote(QUOTE_GOLDEN, flagged)
    print(f"\nquote_analysis eval: {result_score}")
    assert result_score.precision >= MISSING_GOVT_FEE_PRECISION_THRESHOLD, str(result_score)


# ------------------------------------------------------------------- A2 matching


def test_the_matching_golden_set_is_large_enough() -> None:
    assert len(MATCHING_GOLDEN) >= 20
    assert len({case.id for case in MATCHING_GOLDEN}) == len(MATCHING_GOLDEN)


def test_every_case_has_an_annotated_ideal_inside_its_own_pool() -> None:
    """An ideal naming a company that was never a candidate would score the
    agent on a choice it was never offered."""
    for case in MATCHING_GOLDEN:
        pool = {candidate["provider_id"] for candidate in case.candidates}
        assert case.ideal, case.id
        assert set(case.ideal) <= pool, case.id
        assert len(case.candidates) >= 4, case.id


def test_every_case_holds_a_candidate_that_does_not_belong_in_the_top() -> None:
    """A pool where everybody is a good answer scores nothing."""
    for case in MATCHING_GOLDEN:
        pool = {candidate["provider_id"] for candidate in case.candidates}
        assert pool - set(case.ideal), case.id


def test_the_perfect_order_scores_one_and_the_reverse_scores_less() -> None:
    ideal = ("a", "b", "c")

    assert ndcg_at_k(["a", "b", "c"], ideal) == 1.0
    assert ndcg_at_k(["c", "b", "a"], ideal) < 1.0
    assert ndcg_at_k(["x", "y"], ideal) == 0.0


def test_matching_scoring_averages_over_the_set() -> None:
    cases = [
        MatchingCase("a", {}, [], ("p1", "p2")),
        MatchingCase("b", {}, [], ("p1", "p2")),
    ]

    result = score_matching(cases, {"a": (["p1", "p2"], 4, 0), "b": ([], 0, 0)})

    assert result.ndcg == 0.5
    assert result.grounding_violation_rate == 0.0


def test_matching_scoring_reports_a_violation_that_reached_the_buyer() -> None:
    cases = [MatchingCase("a", {}, [], ("p1",))]

    result = score_matching(cases, {"a": (["p1"], 3, 1)})

    assert result.grounding_violation_rate > MAX_GROUNDING_VIOLATION_RATE


def test_the_fallback_publishes_nothing_ungrounded_across_the_whole_set() -> None:
    """The floor with the model switched off, and the one number AI_AGENTS A2
    puts at zero. Screening is a fixed point: what survives it survives it
    again, so nothing reaches a buyer that the company's own profile does not
    bear out."""
    agent = MatchingAgent()

    for case in MATCHING_GOLDEN:
        ctx = {**case.rfq, "candidates": case.candidates}
        screened = screen_matches(agent.fallback(ctx, "disabled"), case.candidates)
        by_id = {candidate["provider_id"]: candidate for candidate in case.candidates}
        for item in screened.items:
            candidate = by_id[item.provider_id]
            assert ground_reasons(item.reasons, candidate) == item.reasons, case.id
            assert ground_reasons(item.concerns, candidate) == item.concerns, case.id


def test_the_fallback_puts_at_least_something_ideal_on_the_page() -> None:
    """Not a threshold - the fallback has not read the buyer's paragraph - but
    a shortlist that never contains a right answer would be worse than none."""
    agent = MatchingAgent()
    hits = 0

    for case in MATCHING_GOLDEN:
        ctx = {**case.rfq, "candidates": case.candidates}
        out = agent.fallback(ctx, "disabled")
        ranked = [item.provider_id for item in out.items][:5]
        hits += int(bool(set(ranked) & set(case.ideal)))

    assert hits == len(MATCHING_GOLDEN)


@pytest.mark.eval
def test_matching_against_the_real_api(settings: Any) -> None:
    """Run manually: ``uv run pytest -m eval``. Costs real money."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or key.startswith("test-"):
        pytest.skip("no real ANTHROPIC_API_KEY configured")
    settings.ANTHROPIC_API_KEY = key
    settings.AGENTS_ENABLED = True

    agent = MatchingAgent()
    produced: dict[str, tuple[list[str], int, int]] = {}
    for case in MATCHING_GOLDEN:
        ctx = {"object_id": case.id, **case.rfq, "candidates": case.candidates}
        result = agent.run(ctx)
        assert not result.used_fallback, f"{case.id} fell back: {result.fallback_reason}"
        screened = screen_matches(result.data, case.candidates)  # type: ignore[arg-type]
        by_id = {candidate["provider_id"]: candidate for candidate in case.candidates}
        published = ungrounded = 0
        for item in screened.items:
            candidate = by_id[item.provider_id]
            sentences = list(item.reasons) + list(item.concerns)
            published += len(sentences)
            ungrounded += len(sentences) - len(ground_reasons(sentences, candidate))
        produced[case.id] = ([item.provider_id for item in screened.items], published, ungrounded)

    result_score = score_matching(MATCHING_GOLDEN, produced)
    print(f"\nmatching eval: {result_score}")
    assert result_score.ndcg >= MATCHING_NDCG_THRESHOLD, str(result_score)
    assert result_score.grounding_violation_rate <= MAX_GROUNDING_VIOLATION_RATE, str(result_score)
