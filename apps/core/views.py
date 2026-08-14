"""Operational endpoints."""

from typing import Any

from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from apps.registry.selectors import registry_last_synced_at


def home(request: HttpRequest) -> HttpResponse:
    """Landing page: the pitch plus a way into the directory.

    Carries the sync time because it states that the register is synced daily
    (COMPLIANCE section 1): a claim about freshness has to be checkable on the
    page that makes it.
    """
    return render(
        request, "pages/home.html", {"registry_last_synced_at": registry_last_synced_at()}
    )


def _check_database() -> tuple[bool, str | None]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        return False, str(exc)
    return True, None


def _check_redis() -> tuple[bool, str | None]:
    from django.conf import settings

    try:
        import redis

        # redis-py ships no annotation for from_url.
        client = redis.from_url(  # type: ignore[no-untyped-call]
            settings.REDIS_URL, socket_connect_timeout=2
        )
        client.ping()
    except Exception as exc:
        return False, str(exc)
    return True, None


@never_cache
def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness/readiness probe: 200 when DB and Redis are both reachable."""
    db_ok, db_error = _check_database()
    redis_ok, redis_error = _check_redis()

    payload: dict[str, Any] = {
        "status": "ok" if (db_ok and redis_ok) else "degraded",
        "checks": {
            "database": {"ok": db_ok, "error": db_error},
            "redis": {"ok": redis_ok, "error": redis_error},
        },
    }
    return JsonResponse(payload, status=200 if (db_ok and redis_ok) else 503)
