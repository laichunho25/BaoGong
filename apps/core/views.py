"""Operational endpoints."""

from typing import Any

from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from apps.providers.selectors import popular_searches, service_summaries
from apps.registry.selectors import market_snapshot, registry_last_synced_at
from apps.reviews.selectors import featured_reviews
from apps.rfq.selectors import matching_snapshot, open_rfqs

#: How many open requirements to preview for a signed-in visitor.
HOME_RFQ_PREVIEW = 3


def home(request: HttpRequest) -> HttpResponse:
    """Landing page: what the platform knows, in the order a buyer asks for it.

    Everything on this page is a live read. There is no editorial slot, no
    hand-picked provider and no illustrative figure - the section that shows
    reviews shows the verified ones, the section that counts companies counts
    rows, and the market numbers carry the sync time beside them because a
    claim about freshness has to be checkable on the page making it
    (COMPLIANCE section 1).

    The open-requirement previews are the one part that depends on who is
    looking. Counts are public; the requirements themselves are not, for the
    same reason the wall sits behind a login (COMPLIANCE section 4).
    """
    context = {
        "registry_last_synced_at": registry_last_synced_at(),
        "market": market_snapshot(),
        "services": service_summaries(),
        "chips": popular_searches(),
        "reviews": featured_reviews(),
        "matching": matching_snapshot(),
        "rfq_previews": (open_rfqs()[:HOME_RFQ_PREVIEW] if request.user.is_authenticated else None),
    }
    return render(request, "pages/home.html", context)


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
