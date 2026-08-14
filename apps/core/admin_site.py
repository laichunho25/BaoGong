"""The internal console, mounted somewhere other than ``/admin/``.

The admin is the highest-value target on the site: it edits every table, and
``/admin/`` is the first path any scanner tries. Two changes make it much less
findable, neither of which is a substitute for the other:

* it is mounted on a secret prefix (``ADMIN_URL``), so an unauthenticated
  probe of ``/admin/`` gets the ordinary 404 page and learns nothing;
* an authenticated account that is not staff gets 404 rather than the login
  form, so a curious buyer who guesses the prefix cannot confirm the guess.

The branding is deliberately neutral: a screenshot of this console should not
advertise which product it belongs to.
"""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Any

from django.contrib import admin
from django.contrib.admin.apps import AdminConfig
from django.http import Http404
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest, HttpResponse


class HardenedAdminSite(admin.AdminSite):
    site_header = _("Internal console")
    site_title = _("Internal console")
    index_title = _("Operations")
    # Shown on the login page; kept free of the product name for the same
    # reason as the header.
    enable_nav_sidebar = True

    def has_permission(self, request: HttpRequest) -> bool:
        """Staff only - the same rule as Django's, stated here on purpose.

        Role is not checked in addition: ``is_staff`` is the flag the whole
        admin already keys off, and a second, parallel gate would eventually
        disagree with it.
        """
        user = request.user
        return bool(user.is_active and user.is_staff)

    # django-stubs types this with its private _ViewType alias; the signature
    # below is the same callable, spelled in public types.
    def admin_view(  # type: ignore[override]
        self, view: Callable[..., Any], cacheable: bool = False
    ) -> Callable[..., Any]:
        """Answer 404 to a signed-in account that has no business here.

        Django's default sends such an account to the login page, which then
        says "you are authenticated as X, but are not authorized" - confirming
        to any signed-in visitor that they have found the console. Anonymous
        visitors keep the normal redirect: without it, staff could not log in.
        """
        checked = super().admin_view(view, cacheable)

        @wraps(view)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if request.user.is_authenticated and not self.has_permission(request):
                raise Http404
            response: HttpResponse = checked(request, *args, **kwargs)
            return response

        return wrapper

    def login(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        """Same rule for the login page itself, which is not an ``admin_view``."""
        if request.user.is_authenticated and not self.has_permission(request):
            raise Http404
        return super().login(request, extra_context)


class HardenedAdminConfig(AdminConfig):
    """Installs :class:`HardenedAdminSite` as the default ``admin.site``."""

    default_site = "apps.core.admin_site.HardenedAdminSite"
