"""The agent log screen.

Only what is customised: that a run cannot be edited or deleted, and that a
moderator's verdict on one can be recorded from the changelist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.urls import reverse

from apps.agents import services
from apps.agents.models import AgentRun, FeedbackVerdict

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Review

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:agents_agentrun_changelist"


@pytest.fixture
def staff_moderator(moderator: User) -> User:
    moderator.is_staff = True
    moderator.is_superuser = True
    moderator.save()
    return moderator


@pytest.fixture
def run(
    settings: Any,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> AgentRun:
    settings.AGENTS_ENABLED = False
    review = make_review(
        provider=make_provider(), author=make_user(email="buyer@example.com"), is_verified=False
    )
    services.moderate_review(review)
    return AgentRun.objects.get()


def test_the_log_cannot_be_edited(client: Client, staff_moderator: User, run: AgentRun) -> None:
    """Rule 4's record is worth nothing if the record can be rewritten."""
    client.force_login(staff_moderator)

    response = client.get(reverse("admin:agents_agentrun_change", args=[run.pk]))

    content = response.content.decode()
    assert response.status_code == 200
    assert "_save" not in content
    assert "Delete" not in content


def test_the_output_is_shown_as_what_the_agent_said(
    client: Client, staff_moderator: User, run: AgentRun
) -> None:
    client.force_login(staff_moderator)

    response = client.get(reverse("admin:agents_agentrun_change", args=[run.pk]))

    assert "Advice" in response.content.decode()


def test_recording_a_verdict_asks_first_and_then_saves(
    client: Client, staff_moderator: User, run: AgentRun
) -> None:
    client.force_login(staff_moderator)

    response = client.post(
        reverse(CHANGELIST), {"action": "mark_wrong", "_selected_action": [str(run.pk)]}
    )
    assert response.status_code == 200
    assert run.feedback.count() == 0

    client.post(
        reverse(CHANGELIST),
        {
            "action": "mark_wrong",
            "_selected_action": [str(run.pk)],
            "apply_reason": "1",
            "reason": "The review named no third party; the label was wrong.",
        },
    )

    feedback = run.feedback.get()
    assert feedback.verdict == FeedbackVerdict.WRONG
    assert feedback.reviewer_id == staff_moderator.pk


def test_a_verdict_may_be_recorded_without_a_note(
    client: Client, staff_moderator: User, run: AgentRun
) -> None:
    """Unlike a moderation decision, this changes nothing for a user, so the
    note is optional - requiring one would just mean fewer verdicts."""
    client.force_login(staff_moderator)

    client.post(
        reverse(CHANGELIST),
        {
            "action": "mark_correct",
            "_selected_action": [str(run.pk)],
            "apply_reason": "1",
            "reason": "",
        },
    )

    assert run.feedback.get().verdict == FeedbackVerdict.CORRECT
