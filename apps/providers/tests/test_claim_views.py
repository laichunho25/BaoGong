"""The claim pages, and who is allowed to see what.

Denials here are 404 rather than 403 throughout: whether a claim exists for a
given company is itself information, and a 403 would confirm it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.urls import reverse

from apps.core.scanning import ScanStatus
from apps.core.uploads import inspect_upload
from apps.providers import services
from apps.providers.models import ClaimStatus, ProviderClaim

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client

    from apps.accounts.models import User
    from apps.providers.models import ClaimEvidence, Provider

pytestmark = pytest.mark.django_db


def _claim(provider: Provider, user: User) -> ProviderClaim:
    return services.submit_claim(provider=provider, user=user, contact_name="Chan Tai Man")


def _evidence(
    claim: ProviderClaim, upload: SimpleUploadedFile, *, clean: bool = False
) -> ClaimEvidence:
    evidence = services.attach_evidence(
        claim=claim, upload=upload, inspected=inspect_upload(upload)
    )
    if clean:
        evidence.scan_status = ScanStatus.CLEAN
        evidence.save(update_fields=["scan_status"])
    return evidence


class TestClaimStart:
    def test_an_anonymous_visitor_is_sent_to_sign_in(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider()

        response = client.get(reverse("providers:claim_start", args=[provider.slug]))

        assert response.status_code == 302
        assert reverse("accounts:login") in response["Location"]

    def test_an_unverified_account_is_asked_to_verify_first(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()
        user = make_user(verified=False)
        client.force_login(user)

        response = client.get(reverse("providers:claim_start", args=[provider.slug]))

        assert response.status_code == 302
        assert reverse("accounts:verification_sent") in response["Location"]

    def test_an_already_claimed_page_has_no_claim_form(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider(claim_status=ClaimStatus.CLAIMED)
        client.force_login(make_user())

        response = client.get(reverse("providers:claim_start", args=[provider.slug]))

        assert response.status_code == 404

    def test_a_submission_creates_the_claim_and_stores_the_file(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        provider = make_provider()
        user = make_user()
        client.force_login(user)

        response = client.post(
            reverse("providers:claim_start", args=[provider.slug]),
            {
                "contact_name": "Chan Tai Man",
                "contact_role": "Director",
                "contact_phone": "+852 9000 0000",
                "business_registration_no": "12345678-000",
                "website": "https://example.com",
                "evidence_kind": "business_registration",
                "evidence": make_upload(),
                "applicant_note": "",
                "confirms_authority": "on",
            },
        )

        claim = ProviderClaim.objects.get(provider=provider)
        assert response.status_code == 302
        assert response["Location"] == reverse("providers:claim_detail", args=[claim.pk])
        assert claim.submitted_by_id == user.pk
        assert claim.evidence.count() == 1

    def test_a_file_that_is_not_a_document_is_refused_before_anything_is_stored(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        provider = make_provider()
        client.force_login(make_user())

        response = client.post(
            reverse("providers:claim_start", args=[provider.slug]),
            {
                "contact_name": "Chan Tai Man",
                "evidence_kind": "business_registration",
                "evidence": make_upload(content=b"MZ\x90\x00 executable"),
                "confirms_authority": "on",
            },
        )

        assert response.status_code == 200
        assert not ProviderClaim.objects.exists()


class TestClaimDetail:
    def test_the_applicant_sees_the_token_to_publish(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        user = make_user()
        claim = _claim(make_provider(website="https://example.com"), user)
        client.force_login(user)

        response = client.get(reverse("providers:claim_detail", args=[claim.pk]))

        assert response.status_code == 200
        assert claim.website_verification_token in response.content.decode()

    def test_another_account_gets_404_not_403(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        claim = _claim(make_provider(), make_user())
        client.force_login(make_user())

        response = client.get(reverse("providers:claim_detail", args=[claim.pk]))

        assert response.status_code == 404

    def test_a_moderator_may_read_any_claim(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
    ) -> None:
        claim = _claim(make_provider(), make_user())
        client.force_login(moderator)

        assert client.get(reverse("providers:claim_detail", args=[claim.pk])).status_code == 200

    def test_the_applicant_can_run_the_website_check(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from apps.providers import verification

        user = make_user()
        claim = _claim(make_provider(website="https://example.com"), user)
        monkeypatch.setattr(
            services,
            "verify_website",
            lambda website, token: verification.VerificationOutcome(
                verified=True,
                method=verification.METHOD_DNS_TXT,
                attempts=(
                    verification.VerificationAttempt(verification.METHOD_DNS_TXT, True, "matched"),
                ),
            ),
        )
        client.force_login(user)

        response = client.post(reverse("providers:claim_verify", args=[claim.pk]))

        claim.refresh_from_db()
        assert response.status_code == 302
        assert claim.is_website_verified

    def test_the_applicant_can_withdraw(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        user = make_user()
        provider = make_provider()
        claim = _claim(provider, user)
        client.force_login(user)

        response = client.post(reverse("providers:claim_withdraw", args=[claim.pk]))

        claim.refresh_from_db()
        provider.refresh_from_db()
        assert response.status_code == 302
        assert claim.status == "withdrawn"
        assert provider.claim_status == ClaimStatus.UNCLAIMED


class TestEvidenceDownload:
    def test_an_unscanned_file_is_not_served_even_to_its_uploader(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        user = make_user()
        claim = _claim(make_provider(), user)
        evidence = _evidence(claim, make_upload())
        client.force_login(user)

        response = client.get(reverse("providers:claim_evidence", args=[evidence.pk]))

        assert response.status_code == 404

    def test_a_cleared_file_is_served_without_being_cached(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        user = make_user()
        claim = _claim(make_provider(), user)
        evidence = _evidence(claim, make_upload(), clean=True)
        client.force_login(user)

        response = client.get(reverse("providers:claim_evidence", args=[evidence.pk]))

        assert response.status_code == 200
        # Personal data: no shared cache may keep a copy.
        assert response["Cache-Control"] == "private, no-store"

    def test_a_stranger_gets_404(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        claim = _claim(make_provider(), make_user())
        evidence = _evidence(claim, make_upload(), clean=True)
        client.force_login(make_user())

        response = client.get(reverse("providers:claim_evidence", args=[evidence.pk]))

        assert response.status_code == 404


class TestDetailPageCta:
    def test_an_unclaimed_page_offers_the_claim_link(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider()

        response = client.get(provider.get_absolute_url())

        assert reverse("providers:claim_start", args=[provider.slug]) in response.content.decode()

    def test_a_page_under_review_says_so_instead(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()
        _claim(provider, make_user())

        response = client.get(provider.get_absolute_url())

        content = response.content.decode()
        assert reverse("providers:claim_start", args=[provider.slug]) not in content
