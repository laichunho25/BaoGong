"""A counter that says "this has happened too often, from here, lately".

Used by the flows where an unauthenticated visitor can make the server do
something expensive or dangerous on somebody else's behalf: guessing a
password, mailing a reset link to an address they do not own, or asking us to
re-send a verification mail a thousand times.

A cache counter rather than a table, for the same reason the advisor's limiter
is one: the durable version of this is a log of who tried to sign in from
which address, which is personal data we would then have to justify keeping
(COMPLIANCE section 3). The cost of losing the counters on a restart is one
extra allowance, which no attacker can arrange on demand.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from django.core.cache import cache

from apps.core.middleware import client_ip

if TYPE_CHECKING:
    from django.http import HttpRequest


def client_key(request: HttpRequest, *, scope: str, subject: str = "") -> str:
    """A cache key for one caller and one flow.

    The address and the subject (usually an email) are hashed, not stored: the
    cache is a shared service and the keys are visible to anything that can
    read it, so the counter should not double as a list of who tried to sign in
    as whom.
    """
    material = f"{client_ip(request)}|{subject.strip().lower()}"
    return f"throttle:{scope}:{hashlib.sha256(material.encode()).hexdigest()[:32]}"


def too_many(key: str, *, limit: int, window_seconds: int) -> bool:
    """Record one attempt against ``key`` and say whether it is one too many.

    The attempt is counted even when it is refused, so hammering a blocked key
    keeps it blocked rather than letting the window drain while the caller
    keeps trying.
    """
    seen = int(cache.get_or_set(key, 0, window_seconds) or 0)
    cache.set(key, seen + 1, window_seconds)
    return seen >= limit


def forget(key: str) -> None:
    """Drop a counter - called after the attempt it was guarding succeeded."""
    cache.delete(key)
