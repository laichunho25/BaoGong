"""``BaseAgent``: the guarantees the rest of the platform relies on.

Four of them, and each has a test below:

* an agent never raises at its caller - every failure becomes the fallback;
* every call writes exactly one ``AgentRun``, including the ones that never
  reach the API;
* the kill switch and the budget stop the call before a socket is opened;
* a model answer that does not fit the schema is not used.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from apps.agents import pricing
from apps.agents.base import BaseAgent, PromptNotFound, load_prompt
from apps.agents.models import AgentRun, AgentStatus, FallbackReason
from apps.agents.review_moderation import ReviewModerationAgent
from apps.agents.schemas import ModerationOut
from apps.agents.tests.conftest import FakeBlock, FakeResponse

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest import MonkeyPatch

pytestmark = pytest.mark.django_db

CTX: dict[str, Any] = {
    "object_id": "abc",
    "body": "They filed the incorporation in four days and explained every fee.",
    "provider_name": "Example Secretaries Limited",
    "services": ["incorporation"],
    "overall": "4.5",
    "author_verified": True,
}


def test_a_switched_off_agent_falls_back_and_never_builds_a_client(
    settings: Any, monkeypatch: MonkeyPatch
) -> None:
    """The kill switch has to stop the call, not merely discard its result."""

    def explode(self: BaseAgent) -> Any:  # pragma: no cover - must not run
        raise AssertionError("a switched-off agent built a client")

    monkeypatch.setattr(BaseAgent, "_client", explode)
    settings.AGENTS_ENABLED = False

    result = ReviewModerationAgent().run(CTX)

    assert result.used_fallback
    assert result.fallback_reason == FallbackReason.DISABLED


def test_one_agent_can_be_switched_off_without_the_others(settings: Any) -> None:
    settings.AGENTS_ENABLED = True
    settings.AGENT_ENABLED_REVIEW_MODERATION = False

    result = ReviewModerationAgent().run(CTX)

    assert result.fallback_reason == FallbackReason.DISABLED


def test_no_api_key_is_a_fallback_not_a_crash(settings: Any) -> None:
    """A developer without a key gets a working platform, not a stack trace."""
    settings.AGENTS_ENABLED = True
    settings.ANTHROPIC_API_KEY = ""

    result = ReviewModerationAgent().run(CTX)

    assert result.used_fallback
    assert result.fallback_reason == FallbackReason.NO_API_KEY


def test_the_daily_budget_stops_further_calls(settings: Any, enabled: None) -> None:
    """AI_AGENTS principle 4: budget spent means every agent falls back."""
    settings.AGENT_BUDGET_DAILY_USD = Decimal("1.00")
    AgentRun.objects.create(
        agent_name="whatever", model="claude-sonnet-5", input_hash="x", cost_usd=Decimal("1.50")
    )

    result = ReviewModerationAgent().run(CTX)

    assert result.fallback_reason == FallbackReason.BUDGET


def test_a_successful_call_is_logged_with_its_cost_and_tokens(
    enabled: None, fake_client: Callable[..., Any], moderation_payload: dict[str, Any]
) -> None:
    fake_client(FakeResponse([FakeBlock(moderation_payload)], tokens=(1000, 500)))

    result = ReviewModerationAgent().run(CTX)

    assert not result.used_fallback
    run = AgentRun.objects.get()
    assert run.status == AgentStatus.OK
    assert run.agent_name == "review_moderation"
    assert run.prompt_version == "v1"
    assert (run.input_tokens, run.output_tokens) == (1000, 500)
    # 1000 in + 500 out at Sonnet's published prices.
    assert run.cost_usd == pricing.estimate_cost(
        "claude-sonnet-5", input_tokens=1000, output_tokens=500
    )
    assert run.attempts == 1


def test_the_log_holds_a_reference_to_the_input_and_not_the_input(
    enabled: None, fake_client: Callable[..., Any], moderation_payload: dict[str, Any]
) -> None:
    """COMPLIANCE section 4: the cost screen must not become a copy of the reviews."""
    fake_client(FakeResponse([FakeBlock(moderation_payload)]))

    ReviewModerationAgent().run(CTX)

    run = AgentRun.objects.get()
    assert CTX["body"] not in str(run.input_ref)
    assert run.input_ref["body_chars"] == len(CTX["body"])
    assert len(run.input_hash) == 64


def test_output_that_does_not_fit_the_schema_is_not_used(
    enabled: None, fake_client: Callable[..., Any]
) -> None:
    """An invented severity is a prompt problem, and is recorded as one."""
    fake_client(FakeResponse([FakeBlock({"severity": "catastrophic", "confidence": 0.9})]))

    result = ReviewModerationAgent().run(CTX)

    assert result.used_fallback
    assert result.fallback_reason == FallbackReason.INVALID_SCHEMA
    run = AgentRun.objects.get()
    assert run.status == AgentStatus.FALLBACK
    assert run.error


def test_a_forced_tool_call_that_never_arrives_is_treated_as_unusable(
    enabled: None, fake_client: Callable[..., Any]
) -> None:
    fake_client(FakeResponse([]))

    result = ReviewModerationAgent().run(CTX)

    assert result.fallback_reason == FallbackReason.INVALID_SCHEMA


def test_a_transient_error_is_retried_and_the_second_answer_is_used(
    enabled: None, fake_client: Callable[..., Any], moderation_payload: dict[str, Any]
) -> None:
    import anthropic

    boom = anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]
    client = fake_client(boom, FakeResponse([FakeBlock(moderation_payload)]))

    result = ReviewModerationAgent().run(CTX)

    assert not result.used_fallback
    assert len(client.messages.calls) == 2
    assert AgentRun.objects.get().attempts == 2


def test_a_timeout_falls_back_after_the_retries_are_spent(
    enabled: None, fake_client: Callable[..., Any]
) -> None:
    import anthropic

    client = fake_client(anthropic.APITimeoutError(request=None))  # type: ignore[arg-type]

    result = ReviewModerationAgent().run(CTX)

    assert result.fallback_reason == FallbackReason.TIMEOUT
    # max_retries=2 means three attempts in total, and no more.
    assert len(client.messages.calls) == 3


def test_an_unexpected_exception_does_not_reach_the_caller(
    enabled: None, fake_client: Callable[..., Any]
) -> None:
    """A moderation queue must not stop accepting reviews because a vendor broke."""
    client = fake_client(RuntimeError("something entirely unforeseen"))

    result = ReviewModerationAgent().run(CTX)

    assert result.used_fallback
    assert isinstance(result.data, ModerationOut)
    # Not retried: an unrecognised error is not known to be transient.
    assert len(client.messages.calls) == 1


def test_the_call_forces_the_schema_tool(
    enabled: None, fake_client: Callable[..., Any], moderation_payload: dict[str, Any]
) -> None:
    """CLAUDE.md section 5: structured output, never a parse of free text."""
    client = fake_client(FakeResponse([FakeBlock(moderation_payload)]))

    ReviewModerationAgent().run(CTX)

    call = client.messages.calls[0]
    assert call["tool_choice"]["type"] == "tool"
    assert call["tools"][0]["input_schema"]["properties"]["severity"]
    assert call["max_tokens"] == ReviewModerationAgent.max_tokens


def test_every_path_writes_exactly_one_run(settings: Any) -> None:
    settings.AGENTS_ENABLED = False

    ReviewModerationAgent().run(CTX)
    ReviewModerationAgent().run(CTX)

    assert AgentRun.objects.count() == 2


def test_the_same_input_hashes_the_same_way(settings: Any) -> None:
    settings.AGENTS_ENABLED = False

    ReviewModerationAgent().run(CTX)
    ReviewModerationAgent().run(dict(reversed(list(CTX.items()))))

    hashes = set(AgentRun.objects.values_list("input_hash", flat=True))
    assert len(hashes) == 1


def test_a_missing_prompt_file_is_raised_not_swallowed() -> None:
    """A deployment error must not hide behind the fallback rate."""
    with pytest.raises(PromptNotFound):
        load_prompt("this_prompt_does_not_exist_v9.md")


class _NoVersionAgent(ReviewModerationAgent):
    prompt_file: ClassVar[str] = "moderation_v3.md"


def test_the_prompt_version_comes_from_the_filename() -> None:
    assert _NoVersionAgent().prompt_version == "v3"
