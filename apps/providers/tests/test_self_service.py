"""A company editing its own page.

Three rules are load-bearing and each has a test that fails loudly if it is
relaxed: a tier may not write a field it does not pay for, free text never
reaches the page without a human reading it, and a licence that has left the
official register takes the whole platform-supplied half of the page with it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import MemberRole, ProviderMember, Role
from apps.providers import selectors, services
from apps.providers.models import (
    ClaimStatus,
    ProfileEditStatus,
    Provider,
    ProviderProfileEdit,
    ServiceCategory,
    Tier,
)
from apps.providers.tests.conftest import PASSWORD
from apps.registry.models import LicenceStatus, allow_registry_writes

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from apps.accounts.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def claimed(make_provider: Callable[..., Provider]) -> Callable[..., Provider]:
    def _make(**overrides: object) -> Provider:
        return make_provider(claim_status=ClaimStatus.CLAIMED, **overrides)

    return _make


def _member(provider: Provider, user: User) -> ProviderMember:
    return ProviderMember.objects.create(user=user, provider=provider, member_role=MemberRole.OWNER)


def _delist(provider: Provider) -> None:
    with allow_registry_writes():
        licensee = provider.licensee
        assert licensee is not None
        licensee.status = LicenceStatus.INACTIVE
        licensee.save(update_fields=["status"])
    provider.refresh_from_db()


class TestEditPermission:
    def test_a_free_company_may_edit_contact_details_but_not_its_description(
        self, claimed: Callable[..., Provider]
    ) -> None:
        provider = claimed(tier=Tier.FREE)

        fields = services.editable_fields(provider)

        assert "contact_phone" in fields
        assert "description" not in fields
        assert "languages" not in fields

    def test_a_paid_company_may_edit_everything_the_form_offers(
        self, claimed: Callable[..., Provider]
    ) -> None:
        provider = claimed(tier=Tier.VERIFIED)

        assert "description" in services.editable_fields(provider)
        assert "bank_types" in services.editable_fields(provider)

    def test_a_suspended_paid_page_is_treated_as_free(
        self, claimed: Callable[..., Provider]
    ) -> None:
        """A licence that vanished suspends placement (A7); the rights follow it."""
        provider = claimed(tier=Tier.PREMIUM, paid_placement_suspended_at=timezone.now())

        assert "description" not in services.editable_fields(provider)

    def test_an_unclaimed_page_cannot_be_edited_by_anyone(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        permission = services.edit_permission(make_provider())

        assert permission.allowed is False

    def test_the_free_tier_waits_a_year_between_updates(
        self, claimed: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = claimed(tier=Tier.FREE)
        user = make_user()
        services.apply_profile_edit(
            provider=provider, actor=user, values={"contact_phone": "+852 2000 0000"}
        )

        blocked = services.edit_permission(provider)
        assert blocked.allowed is False
        assert blocked.next_allowed_at is not None

        later = timezone.now() + timedelta(days=services.FREE_EDIT_INTERVAL_DAYS + 1)
        assert services.edit_permission(provider, now=later).allowed is True

    def test_a_correction_does_not_wait_and_does_not_start_the_clock(
        self, claimed: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = claimed(tier=Tier.FREE)
        user = make_user()
        services.apply_profile_edit(
            provider=provider, actor=user, values={"contact_phone": "+852 2000 0000"}
        )

        services.apply_profile_edit(
            provider=provider,
            actor=user,
            values={"contact_phone": "+852 2000 0001"},
            is_correction=True,
        )

        provider.refresh_from_db()
        assert provider.contact_phone == "+852 2000 0001"
        # Still measured from the first edit, not from the correction.
        assert services.last_allowance_edit(provider).is_correction is False  # type: ignore[union-attr]

    def test_a_correction_may_not_touch_anything_but_contact_details(
        self, claimed: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = claimed(tier=Tier.PREMIUM)

        with pytest.raises(services.ProfileEditError):
            services.apply_profile_edit(
                provider=provider,
                actor=make_user(),
                values={"description": "We are wonderful."},
                is_correction=True,
            )


class TestApplyProfileEdit:
    def test_structured_changes_apply_at_once_and_are_logged(
        self, claimed: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = claimed(tier=Tier.VERIFIED)

        edit = services.apply_profile_edit(
            provider=provider,
            actor=make_user(),
            values={"contact_email": "hello@example.com", "team_size": 12},
        )

        provider.refresh_from_db()
        assert provider.contact_email == "hello@example.com"
        assert provider.team_size == 12
        assert edit.status == ProfileEditStatus.APPLIED
        assert edit.changes["team_size"] == {"from": None, "to": 12}

    def test_a_field_the_tier_does_not_include_is_refused(
        self, claimed: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = claimed(tier=Tier.FREE)

        with pytest.raises(services.ProfileEditError):
            services.apply_profile_edit(
                provider=provider, actor=make_user(), values={"description": "Best in town."}
            )

    def test_a_description_waits_for_a_human_and_the_page_keeps_the_old_one(
        self, claimed: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = claimed(tier=Tier.VERIFIED)

        edit = services.apply_profile_edit(
            provider=provider,
            actor=make_user(),
            values={"description": "我们专注于跨境电商客户的公司秘书服务。"},
        )

        provider.refresh_from_db()
        assert provider.description == ""
        assert edit.status == ProfileEditStatus.PENDING
        assert edit.submitted_description

    def test_a_banned_claim_is_refused_at_the_keyboard(
        self, claimed: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = claimed(tier=Tier.VERIFIED)

        with pytest.raises(services.ProfileEditError):
            services.apply_profile_edit(
                provider=provider,
                actor=make_user(),
                values={"description": "本公司保证开户成功，100%开户。"},
            )

        assert not ProviderProfileEdit.objects.filter(provider=provider).exists()

    def test_switching_a_service_off_keeps_its_prices(
        self, claimed: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = claimed(tier=Tier.VERIFIED)
        user = make_user()
        services.apply_profile_edit(
            provider=provider,
            actor=user,
            values={"service_categories": [ServiceCategory.INCORPORATION]},
        )

        services.apply_profile_edit(
            provider=provider, actor=user, values={"service_categories": []}
        )

        offering = provider.offerings.get(category=ServiceCategory.INCORPORATION)
        assert offering.is_active is False

    def test_a_delisted_company_cannot_edit_anything(
        self, claimed: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = claimed(tier=Tier.PREMIUM)
        _delist(provider)

        with pytest.raises(services.ProfileEditError):
            services.apply_profile_edit(
                provider=provider, actor=make_user(), values={"contact_phone": "+852 2000 0000"}
            )

    def test_an_edit_that_changes_nothing_is_refused(
        self, claimed: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = claimed(tier=Tier.VERIFIED, contact_phone="+852 2000 0000")

        with pytest.raises(services.ProfileEditError):
            services.apply_profile_edit(
                provider=provider, actor=make_user(), values={"contact_phone": "+852 2000 0000"}
            )


class TestDecideProfileEdit:
    def _pending(self, provider: Provider, user: User) -> ProviderProfileEdit:
        return services.apply_profile_edit(
            provider=provider, actor=user, values={"description": "我们成立于 2010 年。"}
        )

    def test_approval_is_the_only_way_text_reaches_the_page(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
    ) -> None:
        provider = claimed(tier=Tier.VERIFIED)
        edit = self._pending(provider, make_user())

        services.decide_profile_edit(
            edit=edit, reviewer=moderator, approve=True, note="Reads fine."
        )

        provider.refresh_from_db()
        assert provider.description == "我们成立于 2010 年。"

    def test_a_rejection_leaves_the_page_alone_and_keeps_the_reason(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
    ) -> None:
        provider = claimed(tier=Tier.VERIFIED)
        edit = self._pending(provider, make_user())

        services.decide_profile_edit(
            edit=edit, reviewer=moderator, approve=False, note="Unverifiable claim."
        )

        provider.refresh_from_db()
        edit.refresh_from_db()
        assert provider.description == ""
        assert edit.status == ProfileEditStatus.REJECTED
        assert edit.review_note == "Unverifiable claim."

    def test_a_decision_without_a_reason_is_refused(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
    ) -> None:
        edit = self._pending(claimed(tier=Tier.VERIFIED), make_user())

        with pytest.raises(services.ProfileEditError):
            services.decide_profile_edit(edit=edit, reviewer=moderator, approve=True, note="  ")

    def test_a_company_cannot_approve_its_own_words(
        self, claimed: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        user = make_user(role=Role.PROVIDER_MEMBER)
        edit = self._pending(claimed(tier=Tier.VERIFIED), user)

        with pytest.raises(services.ProfileEditError):
            services.decide_profile_edit(edit=edit, reviewer=user, approve=True, note="Fine.")

    def test_a_rejected_edit_does_not_spend_the_annual_allowance(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
    ) -> None:
        provider = claimed(tier=Tier.FREE)
        provider.tier = Tier.VERIFIED
        provider.save(update_fields=["tier"])
        edit = self._pending(provider, make_user())
        services.decide_profile_edit(edit=edit, reviewer=moderator, approve=False, note="No.")

        provider.tier = Tier.FREE
        provider.save(update_fields=["tier"])
        assert services.edit_permission(provider).allowed is True


class TestManagePage:
    def _signed_in_member(
        self, client: Client, provider: Provider, make_user: Callable[..., User]
    ) -> User:
        user = make_user(role=Role.PROVIDER_MEMBER)
        _member(provider, user)
        client.login(email=user.email, password=PASSWORD)
        return user

    def test_a_stranger_gets_404_rather_than_a_denial(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = claimed()
        outsider = make_user()
        client.login(email=outsider.email, password=PASSWORD)

        response = client.get(reverse("providers:manage", args=[provider.slug]))

        assert response.status_code == 404

    def test_a_member_sees_only_the_fields_the_tier_allows(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = claimed(tier=Tier.FREE)
        self._signed_in_member(client, provider, make_user)

        response = client.get(reverse("providers:manage", args=[provider.slug]))

        assert response.status_code == 200
        body = response.content.decode()
        assert 'id="id_contact_phone"' in body
        # The paid-only field is absent from the form, not merely hidden in it.
        assert 'id="id_description"' not in body
        assert set(response.context["form"].fields) == services.FREE_EDITABLE_FIELDS

    def test_posting_a_field_the_tier_excludes_changes_nothing(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = claimed(tier=Tier.FREE)
        self._signed_in_member(client, provider, make_user)

        client.post(
            reverse("providers:manage", args=[provider.slug]),
            {"contact_phone": "+852 2000 0000", "description": "Best in Hong Kong."},
        )

        provider.refresh_from_db()
        # Stored without the spaces it was typed with (core.validators).
        assert provider.contact_phone == "+85220000000"
        assert provider.description == ""
        assert not ProviderProfileEdit.objects.filter(submitted_description__gt="").exists()

    def test_a_delisted_page_offers_no_form_at_all(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = claimed(tier=Tier.PREMIUM)
        self._signed_in_member(client, provider, make_user)
        _delist(provider)

        response = client.get(reverse("providers:manage", args=[provider.slug]))

        assert response.status_code == 200
        assert response.context["form"] is None
        assert 'id="id_contact_phone"' not in response.content.decode()


class TestPublicPage:
    def test_the_description_is_shown_and_labelled_as_the_company_speaking(
        self, client: Client, claimed: Callable[..., Provider]
    ) -> None:
        provider = claimed(tier=Tier.VERIFIED, description="我们专注跨境电商客户。")

        body = client.get(provider.get_absolute_url()).content.decode()

        assert "我们专注跨境电商客户。" in body
        assert "认识这家公司" in body

    def test_a_delisted_company_keeps_its_name_and_loses_everything_we_were_told(
        self, client: Client, claimed: Callable[..., Provider]
    ) -> None:
        provider = claimed(
            tier=Tier.PREMIUM,
            description="我们专注跨境电商客户。",
            contact_phone="+852 2000 0000",
        )
        _delist(provider)

        body = client.get(provider.get_absolute_url()).content.decode()

        assert provider.display_name in body
        assert "我们专注跨境电商客户。" not in body
        assert "+852 2000 0000" not in body
        # COMPLIANCE section 1: the official half of the page does not go away.
        assert provider.licensee is not None
        assert provider.licensee.licence_no in body


class TestSelectors:
    def test_the_dashboard_lists_the_pages_this_account_speaks_for(
        self, claimed: Callable[..., Provider], make_user: Callable[..., User]
    ) -> None:
        provider = claimed()
        user = make_user()
        member = _member(provider, user)

        assert selectors.providers_for_member(str(user.pk)) == [provider]

        member.is_active = False
        member.save(update_fields=["is_active"])
        assert selectors.providers_for_member(str(user.pk)) == []
