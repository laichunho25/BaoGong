# Celery tasks: orchestration only, logic lives in services.py.

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from apps.core.notifications import Notification, deliver

logger = logging.getLogger(__name__)


@shared_task(  # type: ignore[untyped-decorator]
    name="core.send_notification",
    autoretry_for=(Exception,),
    retry_backoff=30,
    max_retries=3,
    time_limit=120,
)
def send_notification(*, template: str, recipients: list[str], context: dict[str, Any]) -> int:
    """Deliver one notification.

    Retried because mail servers refuse connections for reasons that pass, and
    the decision this mail describes has already happened - dropping it leaves
    someone waiting for an answer that will never come. Capped at three tries:
    a permanently wrong address is not something a retry can fix, and the
    decision itself is safely recorded either way.
    """
    sent = deliver(Notification(template=template, recipients=tuple(recipients), context=context))
    if not sent:
        logger.warning("Notification %s was not delivered to %s", template, len(recipients))
    return sent
