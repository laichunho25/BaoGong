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
    MAX_FALSE_ESCALATION_RATE,
    GoldenCase,
    load_golden,
    score,
)
from apps.agents.review_moderation import URGENT_REASONS, ReviewModerationAgent, escalation_reason
from apps.agents.schemas import ESCALATING_LABELS, ModerationLabel

GOLDEN = load_golden("review_moderation")


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
