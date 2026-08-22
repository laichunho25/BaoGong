"""Who may work on a company's page.

Three rules carry the whole feature and each has a test that fails loudly if it
is relaxed: only an owner changes the team, nobody is attached to a company
without accepting an invitation sent to their own mailbox, and a claimed page
never ends up with no active owner.

The last one is the quiet one. There is no platform-side "restore my access"
button, so a company that demoted its only owner would need a moderator to dig
it out by hand - which is why the invariant is enforced in the service rather
than trusted to the buttons the page happens to render.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts import selectors, services
from apps.accounts.models import MemberRole, ProviderMember, ProviderMemberInvite, Role
from apps.accounts.permissions import is_provider_member, is_provider_owner
from apps.registry.models import LicenceStatus, allow_registry_writes

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from apps.accounts.models import User
    from apps.providers.models import Provider

pytestmark = pytest.mark.django_db

PASSWORD = "Correct-Horse9!"


def _member(provider: Provider, user: User, role: str = MemberRole.OWNER) -> ProviderMember:
    return ProviderMember.objects.create(user=user, provider=provider, member_role=role)


def _delist(provider: Provider) -> None:
    with allow_registry_writes():
        licensee = provider.licensee
        assert licensee is not None
        licensee.status = LicenceStatus.INACTIVE
        licensee.save(update_fields=["status"])
    provider.refresh_from_db()


class TestInvite:
    def test_an_invitation_grants_nothing_by_itself(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        """The whole reason there are two steps. A form that attached accounts
        directly would let one company hand a colleague's account the right to
        publish on a licensed company's page without asking them."""
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        invitee = make_user(email="colleague@example.com")

        services.invite_member(provider=provider, actor=owner, email="colleague@example.com")

        assert is_provider_member(invitee, provider) is False

    def test_only_an_owner_may_invite(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = make_provider()
        staff = make_user()
        _member(provider, staff, MemberRole.STAFF)

        with pytest.raises(services.MembershipError):
            services.invite_member(provider=provider, actor=staff, email="x@example.com")

    def test_a_stranger_may_not_invite(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        with pytest.raises(services.MembershipError):
            services.invite_member(
                provider=make_provider(), actor=make_user(), email="x@example.com"
            )

    def test_a_delisted_page_may_not_recruit(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        _delist(provider)

        with pytest.raises(services.MembershipError):
            services.invite_member(provider=provider, actor=owner, email="x@example.com")

    def test_an_existing_member_is_not_invited_twice(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = make_provider()
        owner = make_user(email="owner@example.com")
        _member(provider, owner)

        with pytest.raises(services.MembershipError):
            services.invite_member(provider=provider, actor=owner, email="OWNER@example.com")

    def test_re_inviting_leaves_exactly_one_working_link(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        """A resend has to be safe to click twice on the company's side, and the
        older mail must stop working - it may have been forwarded by then."""
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)

        first = services.invite_member(provider=provider, actor=owner, email="c@example.com")
        second = services.invite_member(provider=provider, actor=owner, email="c@example.com")

        assert selectors.invite_for_token(first.token) is None
        assert selectors.invite_for_token(second.token) is not None
        assert selectors.open_invites(provider).count() == 1

    def test_the_invitation_is_mailed_with_a_link(
        self,
        django_capture_on_commit_callbacks: Callable[..., Any],
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)

        with django_capture_on_commit_callbacks(execute=True):
            issued = services.invite_member(
                provider=provider, actor=owner, email="colleague@example.com"
            )

        [message] = mail.outbox
        assert message.to == ["colleague@example.com"]
        assert issued.token in message.body

    def test_the_raw_token_is_not_stored(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        """Same reason as ``EmailVerification``: a database dump must not be a
        bundle of working access links."""
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)

        issued = services.invite_member(provider=provider, actor=owner, email="c@example.com")

        assert issued.token not in issued.invite.token_hash
        assert ProviderMemberInvite.objects.filter(token_hash=issued.token).exists() is False


class TestAccept:
    def test_accepting_creates_the_membership(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        invitee = make_user(email="colleague@example.com")
        issued = services.invite_member(
            provider=provider, actor=owner, email="colleague@example.com"
        )

        membership = services.accept_invite(token=issued.token, user=invitee)

        assert membership.is_active
        assert membership.member_role == MemberRole.STAFF
        assert is_provider_member(invitee, provider)
        assert is_provider_owner(invitee, provider) is False

    def test_a_forwarded_link_does_not_work(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        """Without this the token is a bearer key: forwarded once, and a
        stranger is writing on a licensed company's public page."""
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        issued = services.invite_member(
            provider=provider, actor=owner, email="colleague@example.com"
        )

        with pytest.raises(services.MembershipError):
            services.accept_invite(token=issued.token, user=make_user(email="other@example.com"))

    def test_it_can_only_be_used_once(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        invitee = make_user(email="colleague@example.com")
        issued = services.invite_member(
            provider=provider, actor=owner, email="colleague@example.com"
        )
        services.accept_invite(token=issued.token, user=invitee)

        with pytest.raises(services.MembershipError):
            services.accept_invite(token=issued.token, user=invitee)

    def test_an_expired_link_does_not_work(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        invitee = make_user(email="colleague@example.com")
        issued = services.invite_member(
            provider=provider, actor=owner, email="colleague@example.com"
        )
        ProviderMemberInvite.objects.filter(pk=issued.invite.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        with pytest.raises(services.MembershipError):
            services.accept_invite(token=issued.token, user=invitee)

    def test_a_revoked_link_does_not_work(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        invitee = make_user(email="colleague@example.com")
        issued = services.invite_member(
            provider=provider, actor=owner, email="colleague@example.com"
        )
        services.revoke_invite(invite=issued.invite, actor=owner)

        with pytest.raises(services.MembershipError):
            services.accept_invite(token=issued.token, user=invitee)

    def test_accepting_verifies_the_address_and_sets_the_role(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        """The token arrived in that mailbox and nowhere else, which is the same
        proof the verification loop asks for."""
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        invitee = make_user(email="colleague@example.com", role=Role.BUYER)
        issued = services.invite_member(
            provider=provider, actor=owner, email="colleague@example.com"
        )

        services.accept_invite(token=issued.token, user=invitee)

        invitee.refresh_from_db()
        assert invitee.is_email_verified
        assert invitee.role == Role.PROVIDER_MEMBER

    def test_a_previously_removed_colleague_can_be_let_back_in(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        colleague = make_user(email="colleague@example.com")
        membership = _member(provider, colleague, MemberRole.STAFF)
        services.deactivate_member(membership=membership, actor=owner)
        issued = services.invite_member(
            provider=provider, actor=owner, email="colleague@example.com"
        )

        services.accept_invite(token=issued.token, user=colleague)

        membership.refresh_from_db()
        assert membership.is_active


class TestRoles:
    def test_ownership_is_handed_over_by_promoting_then_stepping_down(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = make_provider()
        founder = make_user()
        successor = make_user()
        founder_membership = _member(provider, founder)
        successor_membership = _member(provider, successor, MemberRole.STAFF)

        services.set_member_role(
            membership=successor_membership, actor=founder, member_role=MemberRole.OWNER
        )
        services.set_member_role(
            membership=founder_membership, actor=founder, member_role=MemberRole.STAFF
        )

        assert is_provider_owner(successor, provider)
        assert is_provider_owner(founder, provider) is False
        assert is_provider_member(founder, provider)

    def test_the_last_owner_cannot_step_down(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        """Doing it in the other order would leave a claimed page nobody can
        manage, and only a moderator could undo that."""
        provider = make_provider()
        owner = make_user()
        membership = _member(provider, owner)

        with pytest.raises(services.MembershipError):
            services.set_member_role(
                membership=membership, actor=owner, member_role=MemberRole.STAFF
            )

    def test_the_last_owner_cannot_remove_themselves(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = make_provider()
        owner = make_user()
        membership = _member(provider, owner)

        with pytest.raises(services.MembershipError):
            services.deactivate_member(membership=membership, actor=owner)

    def test_staff_may_not_change_roles(
        self, make_provider: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = make_provider()
        _member(provider, make_user())
        staff = make_user()
        membership = _member(provider, staff, MemberRole.STAFF)

        with pytest.raises(services.MembershipError):
            services.set_member_role(
                membership=membership, actor=staff, member_role=MemberRole.OWNER
            )

    def test_removal_keeps_the_row_and_tells_the_person(
        self,
        django_capture_on_commit_callbacks: Callable[..., Any],
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        """Deleted rows would take the trail of who edited what with them, and
        the person losing access is the one who needs to hear about it."""
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        colleague = make_user(email="colleague@example.com")
        membership = _member(provider, colleague, MemberRole.STAFF)

        with django_capture_on_commit_callbacks(execute=True):
            services.deactivate_member(membership=membership, actor=owner)

        membership.refresh_from_db()
        assert membership.is_active is False
        assert is_provider_member(colleague, provider) is False
        [message] = mail.outbox
        assert message.to == ["colleague@example.com"]


class TestTeamPage:
    def test_an_owner_can_invite_from_the_page(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        client.force_login(owner)

        response = client.post(
            reverse("providers:team", kwargs={"slug": provider.slug}),
            {"email": "colleague@example.com", "member_role": MemberRole.STAFF},
        )

        assert response.status_code == 302
        assert selectors.open_invites(provider).count() == 1

    def test_a_staff_member_does_not_get_the_page(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        """404 rather than 403: a staff member editing the page has no business
        learning that a team URL exists."""
        provider = make_provider()
        staff = make_user()
        _member(provider, staff, MemberRole.STAFF)
        client.force_login(staff)

        response = client.get(reverse("providers:team", kwargs={"slug": provider.slug}))

        assert response.status_code == 404

    def test_a_stranger_does_not_get_the_page(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()
        client.force_login(make_user())

        response = client.get(reverse("providers:team", kwargs={"slug": provider.slug}))

        assert response.status_code == 404

    def test_accepting_through_the_link_needs_a_post(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        invitee = make_user(email="colleague@example.com")
        issued = services.invite_member(
            provider=provider, actor=owner, email="colleague@example.com"
        )
        client.force_login(invitee)
        url = reverse("accounts:accept_invite", kwargs={"token": issued.token})

        shown = client.get(url)
        assert shown.status_code == 200
        assert is_provider_member(invitee, provider) is False

        accepted = client.post(url)
        assert accepted.status_code == 302
        assert is_provider_member(invitee, provider)

    def test_the_wrong_account_is_told_which_mailbox_was_invited(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        issued = services.invite_member(
            provider=provider, actor=owner, email="colleague@example.com"
        )
        other = make_user(email="other@example.com")
        client.force_login(other)
        url = reverse("accounts:accept_invite", kwargs={"token": issued.token})

        response = client.post(url)

        assert response.status_code == 200
        assert "colleague@example.com" in response.content.decode()
        assert is_provider_member(other, provider) is False

    def test_a_dead_link_is_a_404_page_not_a_crash(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        client.force_login(make_user())

        response = client.get(
            reverse("accounts:accept_invite", kwargs={"token": "not-a-real-token"})
        )

        assert response.status_code == 404

    def test_an_owner_can_promote_and_remove_from_the_page(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        colleague = make_user()
        membership = _member(provider, colleague, MemberRole.STAFF)
        client.force_login(owner)

        promoted = client.post(
            reverse(
                "providers:member_role",
                kwargs={"slug": provider.slug, "member_id": membership.pk},
            ),
            {"member_role": MemberRole.OWNER},
        )
        membership.refresh_from_db()
        assert promoted.status_code == 302
        assert membership.member_role == MemberRole.OWNER

        removed = client.post(
            reverse(
                "providers:member_remove",
                kwargs={"slug": provider.slug, "member_id": membership.pk},
            )
        )
        membership.refresh_from_db()
        assert removed.status_code == 302
        assert membership.is_active is False

    def test_a_refused_change_comes_back_as_a_message_not_a_500(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        """The last owner stepping down is refused in the service; the page has
        to say so rather than fall over, because the button is rendered."""
        provider = make_provider()
        owner = make_user()
        membership = _member(provider, owner)
        client.force_login(owner)

        response = client.post(
            reverse(
                "providers:member_role",
                kwargs={"slug": provider.slug, "member_id": membership.pk},
            ),
            {"member_role": MemberRole.STAFF},
            follow=True,
        )

        membership.refresh_from_db()
        assert membership.member_role == MemberRole.OWNER
        assert any("拥有者" in str(m) for m in response.context["messages"])

    def test_a_member_of_another_company_is_not_reachable_by_id(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        """The id is in the URL, so the lookup is scoped to the provider in it -
        otherwise one owner could deactivate another company's staff."""
        mine = make_provider()
        theirs = make_provider()
        owner = make_user()
        _member(mine, owner)
        outsider = _member(theirs, make_user(), MemberRole.STAFF)
        client.force_login(owner)

        response = client.post(
            reverse("providers:member_remove", kwargs={"slug": mine.slug, "member_id": outsider.pk})
        )

        outsider.refresh_from_db()
        assert response.status_code == 404
        assert outsider.is_active

    def test_an_owner_can_revoke_an_invitation_from_the_page(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        issued = services.invite_member(
            provider=provider, actor=owner, email="colleague@example.com"
        )
        client.force_login(owner)

        response = client.post(
            reverse(
                "providers:invite_revoke",
                kwargs={"slug": provider.slug, "invite_id": issued.invite.pk},
            )
        )

        assert response.status_code == 302
        assert selectors.open_invites(provider).count() == 0
        assert selectors.invite_for_token(issued.token) is None

    def test_revoking_the_same_invitation_twice_is_a_message_not_a_crash(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        issued = services.invite_member(
            provider=provider, actor=owner, email="colleague@example.com"
        )
        services.revoke_invite(invite=issued.invite, actor=owner)
        client.force_login(owner)

        response = client.post(
            reverse(
                "providers:invite_revoke",
                kwargs={"slug": provider.slug, "invite_id": issued.invite.pk},
            )
        )

        assert response.status_code == 302

    def test_a_delisted_page_offers_no_invite_form(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()
        owner = make_user()
        _member(provider, owner)
        _delist(provider)
        client.force_login(owner)

        response = client.get(reverse("providers:team", kwargs={"slug": provider.slug}))

        assert response.status_code == 200
        assert response.context["form"] is None
