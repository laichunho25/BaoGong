"""Operational endpoints for the register mirror."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.http import JsonResponse
from django.views.decorators.cache import never_cache

from apps.registry import selectors

if TYPE_CHECKING:
    from django.http import HttpRequest


@never_cache
def registry_healthz(request: HttpRequest) -> JsonResponse:
    """503 when the register mirror has gone stale, 200 when it is fresh.

    Deliberately separate from ``/healthz``: that one answers "is this process
    alive", which stays true while the daily sync quietly stops running. This
    one answers "is the data still worth showing", which is the failure nobody
    would otherwise see.

    Unauthenticated, because the point is for an external uptime monitor to
    poll it. The payload carries nothing that is not already published on the
    site or a bare count - see ``RegistryHealth.as_dict``.

    ``?max_age_hours=`` is accepted so a monitor can set its own tolerance
    without a redeploy; a non-numeric value falls back to the default rather
    than erroring, since a monitor cannot act on a 400.
    """
    max_age_hours = selectors.DEFAULT_MAX_SYNC_AGE_HOURS
    raw = request.GET.get("max_age_hours")
    if raw is not None and raw.isdigit() and int(raw) > 0:
        max_age_hours = int(raw)

    health = selectors.registry_health(max_age_hours=max_age_hours)
    return JsonResponse(health.as_dict(), status=200 if health.is_healthy else 503)
