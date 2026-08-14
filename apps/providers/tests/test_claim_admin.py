"""The moderator queue.

The queue is a customised Django admin rather than a bespoke screen, so these
tests cover the parts that are actually customised: the reason gate, the
register comparison, and the refusal to link a file no scanner cleared.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.core.scanning import ScanStatus
from apps.core.uploads import inspect_upload
from apps.providers import services
from apps.providers.models import ClaimDecision, ClaimStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client

    from apps.accounts.models import User
    from apps.providers.models import Provider, ProviderClaim

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:providers_providerclaim_changelist"


def _claim(provider: Provider, user: User) -> ProviderClaim:
    return services.submit_claim(provider=provider, user=user, contact_name="Chan Tai Man")


class TestClaimQueue:
    def test_the_change_page_shows_the_register_beside_the_application(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
    ) -> None:
        provider = make_provider(licensee_kwargs={"name_en": "Comparable Limited"})
        claim = _claim(provider, make_user())
        moderator.is_staff = True
        moderator.is_superuser = True
        moderator.save()
        client.force_login(moderator)

        response = client.get(reverse("admin:providers_providerclaim_change", args=[claim.pk]))

        content = response.content.decode()
        assert response.status_code == 200
        # The official name and licence number are what the applicant's
        # statement has to be judged against.
        assert "Comparable Limited" in content
        assert provider.licensee.licence_no in content

    def test_approving_asks_for_a_reason_before_anything_changes(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        provider = make_provider()
        claim = _claim(provider, make_user())
        upload = make_upload()
        evidence = services.attach_evidence(
            claim=claim, upload=upload, inspected=inspect_upload(upload)
        )
        evidence.scan_status = ScanStatus.CLEAN
        evidence.save(update_fields=["scan_status"])
        moderator.is_staff = True
        moderator.is_superuser = True
        moderator.save()
        client.force_login(moderator)

        # First post: the selection only. The intermediate page comes back and
        # the claim is untouched.
        response = client.post(
            reverse(CHANGELIST),
            {"action": "approve_claims", "_selected_action": [str(claim.pk)]},
        )
        claim.refresh_from_db()
        assert response.status_code == 200
        assert claim.status == ClaimDecision.PENDING

        # Second post: with the reason.
        client.post(
            reverse(CHANGELIST),
            {
                "action": "approve_claims",
                "_selected_action": [str(claim.pk)],
                "apply_reason": "1",
                "reason": "BR certificate matches the register",
            },
        )

        claim.refresh_from_db()
        provider.refresh_from_db()
        assert claim.status == ClaimDecision.APPROVED
        assert claim.decision_reason == "BR certificate matches the register"
        assert claim.reviewer_id == moderator.pk
        assert provider.claim_status == ClaimStatus.CLAIMED

    def test_an_unscanned_file_blocks_approval_from_the_admin_too(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        # The rule lives in services precisely so it cannot be bypassed by
        # coming through a different door.
        claim = _claim(make_provider(), make_user())
        upload = make_upload()
        services.attach_evidence(claim=claim, upload=upload, inspected=inspect_upload(upload))
        moderator.is_staff = True
        moderator.is_superuser = True
        moderator.save()
        client.force_login(moderator)

        client.post(
            reverse(CHANGELIST),
            {
                "action": "approve_claims",
                "_selected_action": [str(claim.pk)],
                "apply_reason": "1",
                "reason": "Looks fine to me",
            },
            follow=True,
        )

        claim.refresh_from_db()
        assert claim.status == ClaimDecision.PENDING

    def test_a_staff_account_that_is_not_a_moderator_cannot_decide(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        # is_staff opens the console; deciding a claim needs the moderator
        # role, and that check is in services, not in the admin's permissions.
        claim = _claim(make_provider(), make_user())
        staff = make_user(email="desk@example.com")
        staff.is_staff = True
        staff.save()
        staff.user_permissions.add(*_claim_permissions())
        client.force_login(staff)

        client.post(
            reverse(CHANGELIST),
            {
                "action": "approve_claims",
                "_selected_action": [str(claim.pk)],
                "apply_reason": "1",
                "reason": "I would like this company on the platform",
            },
            follow=True,
        )

        claim.refresh_from_db()
        assert claim.status == ClaimDecision.PENDING


class TestEvidenceAdmin:
    def test_the_change_page_links_a_cleared_file_and_not_an_unscanned_one(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        claim = _claim(make_provider(), make_user())
        blocked = services.attach_evidence(
            claim=claim, upload=make_upload(), inspected=inspect_upload(make_upload())
        )
        cleared = services.attach_evidence(
            claim=claim, upload=make_upload(), inspected=inspect_upload(make_upload())
        )
        cleared.scan_status = ScanStatus.CLEAN
        cleared.save(update_fields=["scan_status"])
        moderator.is_staff = True
        moderator.is_superuser = True
        moderator.save()
        client.force_login(moderator)

        content = client.get(
            reverse("admin:providers_providerclaim_change", args=[claim.pk])
        ).content.decode()

        assert reverse("providers:claim_evidence", args=[cleared.pk]) in content
        # The queue offers no way to open a file no scanner cleared, so a
        # moderator cannot do it by accident.
        assert reverse("providers:claim_evidence", args=[blocked.pk]) not in content

    def test_releasing_a_file_needs_a_reason_and_is_attributed(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        claim = _claim(make_provider(), make_user())
        upload = make_upload()
        evidence = services.attach_evidence(
            claim=claim, upload=upload, inspected=inspect_upload(upload)
        )
        moderator.is_staff = True
        moderator.is_superuser = True
        moderator.save()
        client.force_login(moderator)
        changelist = reverse("admin:providers_claimevidence_changelist")

        response = client.post(
            changelist,
            {"action": "override_scan", "_selected_action": [str(evidence.pk)]},
        )
        evidence.refresh_from_db()
        assert response.status_code == 200
        assert evidence.scan_status == ScanStatus.PENDING

        client.post(
            changelist,
            {
                "action": "override_scan",
                "_selected_action": [str(evidence.pk)],
                "apply_reason": "1",
                "reason": "No scanner deployed yet; opened in a sandbox",
            },
        )

        evidence.refresh_from_db()
        assert evidence.scan_status == ScanStatus.SKIPPED
        assert evidence.scan_override_by_id == moderator.pk

    def test_an_infected_file_is_refused_even_with_a_reason(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        claim = _claim(make_provider(), make_user())
        upload = make_upload()
        evidence = services.attach_evidence(
            claim=claim, upload=upload, inspected=inspect_upload(upload)
        )
        evidence.scan_status = ScanStatus.INFECTED
        evidence.save(update_fields=["scan_status"])
        moderator.is_staff = True
        moderator.is_superuser = True
        moderator.save()
        client.force_login(moderator)

        client.post(
            reverse("admin:providers_claimevidence_changelist"),
            {
                "action": "override_scan",
                "_selected_action": [str(evidence.pk)],
                "apply_reason": "1",
                "reason": "I am sure it is a false positive",
            },
            follow=True,
        )

        evidence.refresh_from_db()
        assert evidence.scan_status == ScanStatus.INFECTED


def _claim_permissions() -> list[Permission]:
    return list(
        Permission.objects.filter(
            content_type__app_label="providers", codename__endswith="providerclaim"
        )
    )
