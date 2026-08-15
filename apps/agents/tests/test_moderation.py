"""A4's rule layer and its fallback.

The model's opinion is tested by the eval harness, not here. What is tested
here is everything around it: that the fallback catches what rules can catch,
that escalation does not depend on the model agreeing, and - most importantly -
that no path through this agent publishes anything.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.agents.review_moderation import (
    URGENT_REASONS,
    ReviewModerationAgent,
    escalation_reason,
)
from apps.agents.schemas import ModerationOut


def _ctx(body: str) -> dict[str, Any]:
    return {"object_id": "r1", "body": body, "provider_name": "Example Ltd", "services": []}


@pytest.fixture
def agent() -> ReviewModerationAgent:
    return ReviewModerationAgent()


# --------------------------------------------------------------------- fallback


def test_the_fallback_spots_contact_details(agent: ReviewModerationAgent) -> None:
    out = agent.fallback(_ctx("Contact Amy at amy@example.com, she is excellent."), "disabled")

    assert "personal_data_leak" in out.labels
    assert out.recommended_action == "human_review"


def test_the_fallback_spots_a_promised_bank_outcome(agent: ReviewModerationAgent) -> None:
    """COMPLIANCE section 2. A user may write it; the platform may not publish it."""
    out = agent.fallback(_ctx("他们保证开户成功，两周就批了。"), "disabled")

    assert "guarantees_bank_success" in out.labels


def test_the_fallback_flags_a_review_too_short_to_be_useful(
    agent: ReviewModerationAgent,
) -> None:
    out = agent.fallback(_ctx("很好"), "budget")

    assert "non_specific" in out.labels


def test_an_ordinary_review_gets_no_labels_from_the_fallback(
    agent: ReviewModerationAgent,
) -> None:
    """A rule layer that flagged everything would make the queue useless."""
    out = agent.fallback(
        _ctx("They filed the incorporation in four days and itemised every fee."), "disabled"
    )

    assert out.labels == []
    assert out.severity == "none"


def test_the_fallback_never_recommends_publishing(agent: ReviewModerationAgent) -> None:
    """AI_AGENTS A4: the fallback is the human queue, whatever the text says."""
    out = agent.fallback(_ctx("Perfectly ordinary and entirely unremarkable service."), "disabled")

    assert out.recommended_action == "human_review"
    assert out.confidence < 0.5


def test_the_fallback_does_not_repeat_a_label(agent: ReviewModerationAgent) -> None:
    out = agent.fallback(_ctx("Call 9123 4567 or 9876 5432 or write to a@b.com"), "disabled")

    assert out.labels.count("personal_data_leak") == 1


# ------------------------------------------------------------------ escalation


def test_high_severity_escalates_however_confident_the_model_was() -> None:
    data = ModerationOut(severity="high", recommended_action="publish", confidence=0.99)

    assert escalation_reason(data, used_fallback=False) == "high_severity"


@pytest.mark.parametrize("label", ["defamation_risk", "personal_data_leak"])
def test_the_two_irreversible_labels_escalate(label: str) -> None:
    """Neither can be undone by taking the review down afterwards."""
    data = ModerationOut(labels=[label], severity="low", recommended_action="publish")

    assert escalation_reason(data, used_fallback=False) == label
    assert escalation_reason(data, used_fallback=False) in URGENT_REASONS


def test_a_clean_reading_still_needs_a_person() -> None:
    """CLAUDE.md rule 3: A4 has no publishing power, so "routine" still queues."""
    data = ModerationOut(severity="none", recommended_action="publish", confidence=0.95)

    assert escalation_reason(data, used_fallback=False) == "routine"


def test_a_fallback_reading_is_marked_as_unread() -> None:
    data = ModerationOut(severity="none", recommended_action="human_review", confidence=0.3)

    assert escalation_reason(data, used_fallback=True) == "no_agent_reading"


# ---------------------------------------------------------------------- prompt


def test_the_prompt_carries_no_contact_details(agent: ReviewModerationAgent) -> None:
    """COMPLIANCE section 4: A4 is not the documented exception; A3 is."""
    prompt = agent.build_user_prompt(_ctx("Ring Amy on 9123 4567 or amy@example.com."))

    assert "9123 4567" not in prompt
    assert "amy@example.com" not in prompt
    assert "[PHONE]" in prompt
