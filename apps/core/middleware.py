"""Cross-cutting request handling.

Currently one job: keeping the internal console out of reach and out of search
results. The URL prefix is secret (see ``apps.core.admin_site``), but a secret
in a URL leaks - through a proxy log, a browser history, a shared screen - so
the optional IP allowlist here is the second lock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.http import Http404

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest, HttpResponse


def client_ip(request: HttpRequest) -> str:
    """The address to match against the allowlist.

    Behind a proxy the peer address is the proxy, so ``X-Forwarded-For`` has to
    be read - but only when the deployment says there is a proxy, and only its
    **last** entry. The left-hand entries are whatever the client sent and can
    be forged; the last one was appended by our own proxy.
    """
    if settings.ADMIN_TRUST_PROXY_IP:
        forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR", ""))
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return str(request.META.get("REMOTE_ADDR", ""))


class AdminAccessMiddleware:
    """Gate and de-index everything under ``settings.ADMIN_URL``.

    A blocked request gets 404, not 403: a 403 confirms that something is
    there.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self.prefix = "/" + settings.ADMIN_URL.lstrip("/")

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not request.path.startswith(self.prefix):
            return self.get_response(request)

        if not settings.ADMIN_ENABLED:
            raise Http404
        allowlist = settings.ADMIN_IP_ALLOWLIST
        if allowlist and client_ip(request) not in allowlist:
            raise Http404

        response = self.get_response(request)
        # robots.txt cannot carry the path - listing it there would publish the
        # secret - so the header is what keeps the console out of an index if
        # a URL ever escapes.
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response
