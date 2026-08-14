"""Cloudflare Turnstile verification.

Turnstile rather than reCAPTCHA: the target audience is in mainland China,
where Google's endpoints are not reachable, so a reCAPTCHA widget would simply
hang for the people this platform exists for (PROMPT_LIBRARY P3).

When ``TURNSTILE_SECRET`` is unset the challenge is treated as passed. That is
the local-development and test case; production sets the key, and
``config.settings.prod`` is where a missing key should fail loudly.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 5

FIELD_NAME = "cf-turnstile-response"


def is_enabled() -> bool:
    return bool(settings.TURNSTILE_SECRET)


def verify(token: str, *, remote_ip: str | None = None) -> bool:
    """Return whether Cloudflare accepts this challenge response.

    A network failure counts as a pass. Turnstile guards a registration form,
    not a payment: an outage at Cloudflare must not lock every new user out of
    the platform, and the email verification loop is still in front of them.
    """
    if not is_enabled():
        return True
    if not token:
        return False

    payload = {"secret": settings.TURNSTILE_SECRET, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    try:
        response = requests.post(VERIFY_URL, data=payload, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return bool(response.json().get("success"))
    except (requests.RequestException, ValueError):
        logger.warning("Turnstile verification unavailable; allowing the request", exc_info=True)
        return True
