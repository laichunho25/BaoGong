"""The notification plumbing.

The decision-to-mail wiring is tested in the apps that make the decisions.
What is tested here is the plumbing's own promises: that a mail is never sent
before the decision it describes is committed, that a failing mail server
cannot take the decision down with it, and that nothing is queued that the
worker will choke on four hours later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.core import mail
from django.db import transaction
from django.test import override_settings

from apps.core.notifications import Notification, absolute_url, deliver, notify, render

if TYPE_CHECKING:
    from collections.abc import Callable


def _notification(**overrides: object) -> Notification:
    context = {
        "provider_name": "ABC Secretaries Limited",
        "url": "https://example.com/p/abc/",
        "sla_days": 5,
    }
    fields: dict[str, object] = {
        "template": "provider_new_review",
        "recipients": ("member@example.com",),
        "context": context,
    }
    fields.update(overrides)
    return Notification(**fields)  # type: ignore[arg-type]


@override_settings(SITE_URL="https://qs.example")
def test_links_are_absolute() -> None:
    """Workers have no request to build from; a relative link in a mail is a
    link that goes nowhere."""
    assert absolute_url("/providers/abc/") == "https://qs.example/providers/abc/"


def test_a_subject_can_never_carry_a_newline() -> None:
    """Template files end in a newline whether the author wanted one or not,
    and a newline in a subject is a header injection."""
    subject, _body = render(_notification())

    assert "\n" not in subject
    assert subject.strip() == subject


def test_the_body_says_what_it_is_for_without_quoting_the_review() -> None:
    _subject, body = render(_notification())

    assert "https://example.com/p/abc/" in body
    # COMPLIANCE section 7: a mail from the platform still has to say the
    # platform is not the government.
    assert "不是政府机构" in body


@pytest.mark.django_db
def test_nothing_is_sent_until_the_decision_commits(
    django_capture_on_commit_callbacks: Callable[..., object],
) -> None:
    """The order matters in one direction only: a mail about a decision that
    was rolled back cannot be recalled."""
    with transaction.atomic():
        notify(
            template="provider_new_review",
            recipients=["member@example.com"],
            context={"provider_name": "ABC", "url": "/p/abc/", "sla_days": 5},
        )
        assert mail.outbox == []


@pytest.mark.django_db
def test_a_queued_notification_is_delivered(
    django_capture_on_commit_callbacks: Callable[..., object],
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        notify(
            template="provider_new_review",
            recipients=["member@example.com", " member@example.com ", ""],
            context={"provider_name": "ABC", "url": "/p/abc/", "sla_days": 5},
        )

    assert len(mail.outbox) == 1
    # The same address twice is one mail, and a blank address is not a
    # recipient - both are ordinary states once emails come from a queryset.
    assert mail.outbox[0].to == ["member@example.com"]


@pytest.mark.django_db
def test_a_company_with_nobody_to_write_to_is_not_an_error(
    django_capture_on_commit_callbacks: Callable[..., object],
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        notify(template="provider_new_review", recipients=[], context={})

    assert mail.outbox == []


@pytest.mark.django_db
def test_a_context_the_worker_could_not_read_fails_here() -> None:
    """Passing a model instance is the easy mistake. It should fail on the
    developer's test run, not in a retry loop hours later."""
    with pytest.raises(TypeError, match="JSON-serialisable"):
        notify(
            template="provider_new_review",
            recipients=["member@example.com"],
            context={"provider": object()},
        )


@override_settings(NOTIFICATIONS_ENABLED=False)
def test_the_switch_stops_delivery_without_stopping_anything_else() -> None:
    sent = deliver(_notification())

    assert sent == 0
    assert mail.outbox == []


@override_settings(NOTIFICATIONS_ENABLED=False)
def test_an_undelivered_notification_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The decision has already happened. A worker that raises here would retry
    three times and then lose the only record that anyone tried to say so."""
    from apps.core.tasks import send_notification

    with caplog.at_level("WARNING"):
        sent = send_notification(
            template="provider_new_review",
            recipients=["member@example.com"],
            context={"provider_name": "ABC", "url": "/p/abc/", "sla_days": 5},
        )

    assert sent == 0
    assert "was not delivered" in caplog.text
