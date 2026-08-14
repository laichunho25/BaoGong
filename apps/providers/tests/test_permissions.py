"""The two authorisation questions the platform actually asks.

These are the seam that replaces django-guardian, so they are tested directly
rather than only through the views that call them. The module under test lives
in ``accounts``; the tests live here because both questions are answered from
``ProviderMember``, and the provider fixtures are here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404, HttpResponse
from django.test import RequestFactory

from apps.accounts import permissions
from apps.accounts.models import ProviderMember, Role

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider

pytestmark = pytest.mark.django_db


def _ok(request: object) -> HttpResponse:
    return HttpResponse("ok")


class TestIsProviderMember:
    def test_an_active_membership_grants_access_by_id_or_instance(
        self,
        make_user: Callable[..., User],
        make_provider: Callable[..., Provider],
    ) -> None:
        user = make_user()
        provider = make_provider()
        ProviderMember.objects.create(user=user, provider=provider)

        assert permissions.is_provider_member(user, provider)
        assert permissions.is_provider_member(user, str(provider.pk))
        assert permissions.member_providers(user) == [str(provider.pk)]

    def test_a_deactivated_membership_does_not(
        self,
        make_user: Callable[..., User],
        make_provider: Callable[..., Provider],
    ) -> None:
        # Deactivating is how a company removes a former employee, so it has to
        # be the same thing as never having been a member.
        user = make_user()
        provider = make_provider()
        ProviderMember.objects.create(user=user, provider=provider, is_active=False)

        assert permissions.is_provider_member(user, provider) is False

    def test_an_anonymous_visitor_is_nobody(self, make_provider: Callable[..., Provider]) -> None:
        anonymous = AnonymousUser()

        assert permissions.is_provider_member(anonymous, make_provider()) is False  # type: ignore[arg-type]
        assert permissions.member_providers(anonymous) == []  # type: ignore[arg-type]


class TestModeratorRequired:
    def test_an_anonymous_visitor_is_sent_to_sign_in(self) -> None:
        request = RequestFactory().get("/queue/")
        request.user = AnonymousUser()  # type: ignore[attr-defined]

        response = permissions.moderator_required(_ok)(request)

        assert response.status_code == 302

    def test_a_signed_in_stranger_gets_404_not_403(self, make_user: Callable[..., User]) -> None:
        # 403 would confirm that there is a queue to be kept out of.
        request = RequestFactory().get("/queue/")
        request.user = make_user()  # type: ignore[attr-defined]

        with pytest.raises(Http404):
            permissions.moderator_required(_ok)(request)

    def test_a_moderator_passes(self, make_user: Callable[..., User]) -> None:
        request = RequestFactory().get("/queue/")
        request.user = make_user(role=Role.MODERATOR)  # type: ignore[attr-defined]

        assert permissions.moderator_required(_ok)(request).status_code == 200
