"""Authorisation helpers.

The platform asks exactly two object-level questions:

* is this user an active member of this provider?
* may this user act on the moderation queue?

Both are answered here, from ``ProviderMember`` and ``User.role``. There is no
per-object ACL table and no django-guardian: a generic permission store would
add rows to maintain, migrate and back up, plus a second place where "who may
edit this page" is defined, in exchange for a granularity nothing in the
product uses. If a real per-object matrix is ever needed - per-field editing
rights, delegated moderation - this module is the seam to replace, and the
callers below do not change.

Denials are 404 rather than 403 wherever the resource's existence is itself
information: the moderation queue should not confirm that a claim exists to
somebody who may not see it.
"""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Any

from django.contrib.auth.views import redirect_to_login
from django.http import Http404

from apps.accounts.models import MemberRole, ProviderMember

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest, HttpResponse

    from apps.accounts.models import User
    from apps.providers.models import Provider


def is_provider_member(user: User, provider: Provider | str) -> bool:
    """Whether ``user`` may act for ``provider``. Accepts an id or an instance."""
    if not user.is_authenticated or not user.is_active:
        return False
    provider_id = provider if isinstance(provider, str) else provider.pk
    return ProviderMember.objects.filter(
        user=user, provider_id=provider_id, is_active=True
    ).exists()


def is_provider_owner(user: User, provider: Provider | str) -> bool:
    """Whether ``user`` may decide who else works on ``provider``'s page.

    Staff members edit the page; owners decide who is a member at all. The
    split exists so that handing a colleague the keys to the profile form does
    not also hand them the ability to lock the founder out.
    """
    if not user.is_authenticated or not user.is_active:
        return False
    provider_id = provider if isinstance(provider, str) else provider.pk
    return ProviderMember.objects.filter(
        user=user, provider_id=provider_id, is_active=True, member_role=MemberRole.OWNER
    ).exists()


def member_providers(user: User) -> list[str]:
    """Ids of the providers ``user`` may act for."""
    if not user.is_authenticated:
        return []
    return [
        str(pk)
        for pk in ProviderMember.objects.filter(user=user, is_active=True).values_list(
            "provider_id", flat=True
        )
    ]


def is_moderator(user: User) -> bool:
    return bool(user.is_authenticated and user.is_active and user.is_moderator)


def moderator_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Restrict a view to the moderation team.

    An anonymous visitor is sent to the login page; a signed-in visitor who is
    not a moderator gets 404, for the same reason the internal console does.
    """

    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not is_moderator(request.user):
            raise Http404
        return view(request, *args, **kwargs)

    return wrapper


def verified_email_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Gate an action that speaks to a real company on a working mailbox.

    Registration deliberately does not block browsing on verification, so the
    check has to live on the actions themselves.
    """
    from django.contrib import messages
    from django.shortcuts import redirect
    from django.utils.translation import gettext as _

    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_email_verified:
            messages.warning(request, _("请先验证邮箱，再提交认领申请。"))
            return redirect("accounts:verification_sent")
        return view(request, *args, **kwargs)

    return wrapper
