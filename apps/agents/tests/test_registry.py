"""The agent listing, and what the compliance checklist asks of it.

COMPLIANCE section 8 requires every agent to have a kill switch. A list that
somebody forgot to add an agent to is a checklist item that passes while the
thing it checks is false, so the listing is asserted against the settings and
against the prompt files here rather than by reading it.
"""

from __future__ import annotations

from django.conf import settings

from apps.agents.base import PROMPT_DIR
from apps.agents.registry import AGENTS, get_agent, kill_switch_setting


def test_every_agent_can_be_had_by_name() -> None:
    for name in AGENTS:
        assert get_agent(name).name == name


def test_every_agent_has_its_own_kill_switch() -> None:
    for name in AGENTS:
        assert hasattr(settings, kill_switch_setting(name)), name


def test_every_agent_has_a_versioned_prompt_on_disk() -> None:
    """CLAUDE.md section 5: prompts live in files with a version in the name,
    never inline in Python, so a change to one is a diff somebody can read."""
    for name, agent in AGENTS.items():
        path = PROMPT_DIR / agent.prompt_file
        assert path.is_file(), name
        assert agent().prompt_version.startswith("v"), name


def test_every_agent_uses_a_model_the_policy_names() -> None:
    """CLAUDE.md section 5's table. A model chosen per agent is a cost and a
    quality decision, and neither should change by accident."""
    allowed = {"claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-opus-5"}

    for name, agent in AGENTS.items():
        assert agent.model in allowed, name
