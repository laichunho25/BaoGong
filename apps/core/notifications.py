"""Outbound notifications: telling people what was decided about them.

Every decision this platform makes about a person - a claim approved, a review
hidden, an NNC1 refused, a dispute settled - is made by a named human who had
to type a reason. Until this module existed, that reason lived only on a page
the person had to think to visit. COMPLIANCE section 3 promises the company an
answer, and an answer nobody is told about is not one.

Three rules hold this module together:

* **A mail can never undo a decision.** Delivery is dispatched from
  ``transaction.on_commit`` into a worker, so a refused SMTP connection cannot
  roll back a moderator's ruling, and nothing is announced before it is
  durable. A decision that stands but was never mailed is a bad day; a mail
  that rolls back a decision is a corrupt record.
* **The mail carries the decision and a link, never the evidence.** Mail sits
  on relays and in mailboxes the platform does not control, so no review
  bodies, no NNC1 fields, no file names travel in it (CLAUDE.md rule 5). The
  recipient signs in to read the thing itself.
* **The reason always travels with the outcome.** Every notification built
  here takes the moderator's own words. A bare "your application was refused"
  is the failure mode this module exists to prevent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

TEMPLATE_DIR = "emails"


@dataclass(frozen=True, slots=True)
class Notification:
    """One rendered-on-delivery message.

    Held as template name plus context rather than as rendered text because it
    crosses a queue boundary: the worker renders in the recipient's language,
    and a subject line fixed at dispatch time would be frozen in whatever
    locale the moderator's admin session happened to be using.
    """

    template: str
    recipients: tuple[str, ...]
    context: dict[str, Any]


def absolute_url(path: str) -> str:
    """Turn a site path into a link that survives leaving the site.

    Workers have no request to build from, and a mail with ``/reviews/mine/``
    in it is a mail with a dead link in it.
    """
    return str(urljoin(settings.SITE_URL, path))


def render(notification: Notification) -> tuple[str, str]:
    """Return ``(subject, body)``. Subjects live in their own template file so
    a translator never has to guess which line of a body becomes the subject."""
    subject = render_to_string(
        f"{TEMPLATE_DIR}/{notification.template}.subject.txt", notification.context
    )
    body = render_to_string(f"{TEMPLATE_DIR}/{notification.template}.txt", notification.context)
    # A subject with a newline in it is a header-injection bug, and template
    # files end with one whether the author wanted it or not.
    return " ".join(subject.split()), body.strip() + "\n"


def deliver(notification: Notification) -> int:
    """Render and send. Called by the worker, not by callers of ``notify``."""
    if not settings.NOTIFICATIONS_ENABLED:
        logger.info(
            "Notifications disabled; dropping %s to %s recipient(s)",
            notification.template,
            len(notification.recipients),
        )
        return 0

    subject, body = render(notification)
    return send_mail(
        subject=subject,
        message=body,
        from_email=None,
        recipient_list=list(notification.recipients),
    )


def notify(*, template: str, recipients: Iterable[str], context: dict[str, Any]) -> None:
    """Queue one notification for after the current transaction commits.

    Silently does nothing when there is nobody to tell - a company with no
    active member is a real state, not an error worth failing a decision over.

    The context is checked for JSON-serialisability here rather than in the
    worker: passing a model instance is the easy mistake, and it should fail in
    the moderator's face on a test run, not four hours later in a retry loop.
    """
    to = tuple(sorted({address.strip() for address in recipients if address and address.strip()}))
    if not to:
        logger.info("No recipients for %s; nothing queued", template)
        return

    try:
        json.dumps(context)
    except TypeError as exc:
        raise TypeError(
            f"Notification context for {template!r} must be JSON-serialisable "
            "so it can cross the queue; pass ids and strings, not model instances."
        ) from exc

    from apps.core.tasks import send_notification

    transaction.on_commit(
        lambda: send_notification.delay(template=template, recipients=list(to), context=context)
    )
