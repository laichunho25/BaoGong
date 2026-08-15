"""Fixtures for the agent layer.

The one thing every test here needs is a way to answer for the Anthropic API
without touching it. ``fake_client`` does that by replacing ``BaseAgent._client``
- the single seam through which any call leaves this codebase - so a test that
forgets to use it gets a fallback, not a network request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from apps.agents.base import TOOL_NAME, BaseAgent
from apps.agents.schemas import ModerationOut
from apps.reviews.tests.conftest import (  # noqa: F401  (re-exported fixtures)
    make_licensee,
    make_provider,
    make_review,
    make_upload,
    make_user,
    moderator,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest import MonkeyPatch


class FakeBlock:
    """One content block in a fake response."""

    def __init__(self, payload: dict[str, Any], *, name: str = TOOL_NAME) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = payload


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(self, content: list[Any], *, tokens: tuple[int, int] = (1200, 300)) -> None:
        self.content = content
        self.usage = FakeUsage(*tokens)


class FakeMessages:
    def __init__(self, outcomes: list[Any]) -> None:
        #: Consumed one per attempt, so a test can make the first call fail and
        #: the second succeed and assert on the retry.
        self._outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.messages = FakeMessages(outcomes)


@pytest.fixture
def fake_client(monkeypatch: MonkeyPatch) -> Callable[..., FakeClient]:
    """Install a stand-in Anthropic client and enable agents for this test."""

    def _install(*outcomes: Any) -> FakeClient:
        client = FakeClient(list(outcomes))
        monkeypatch.setattr(BaseAgent, "_client", lambda self: client)
        # Retries must not put half a second of real sleep into the suite.
        monkeypatch.setattr(BaseAgent, "backoff_base_s", 0.0)
        return client

    return _install


@pytest.fixture
def enabled(settings: Any) -> None:
    """Turn the kill switch back on. Tests using the model path ask for this."""
    settings.AGENTS_ENABLED = True


@pytest.fixture
def moderation_payload() -> dict[str, Any]:
    """A well-formed A4 answer, as the model would send it."""
    return ModerationOut(
        labels=["non_specific"],
        severity="low",
        reasons=["No service, price or timeline is described."],
        suggested_redactions=[],
        recommended_action="human_review",
        confidence=0.72,
    ).model_dump(mode="json")
