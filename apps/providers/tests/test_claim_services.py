"""The claim lifecycle.

The tests are written around the promises the flow makes to three different
people: the applicant (their application cannot be lost or silently rewritten),
the moderator (nothing is approved on evidence nobody could open), and the
visitor (a badge means a human connected this account to the official
register).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import ProviderMember, Role
from apps.core.scanning import ScanResult, ScanStatus
from apps.core.uploads import MAX_UPLOAD_BYTES, inspect_upload
from apps.providers import services
from apps.providers.models import (
    CertificationType,
    ClaimDecision,
    ClaimStatus,
    ProviderClaim,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.accounts.models import User
    from apps.providers.models import ClaimEvidence, Provider

pytestmark = pytest.mark.django_db


def _submit(provider: Provider, user: User) -> ProviderClaim:
    return services.submit_claim(
        provider=provider,
        user=user,
        contact_name="Chan Tai Man",
        website="https://example.com",
    )


def _attach(
    claim: ProviderClaim, upload: SimpleUploadedFile, *, clean: bool = False
) -> ClaimEvidence:
    evidence = services.attach_evidence(
        claim=claim, upload=upload, inspected=inspect_upload(upload)
    )
    if clean:
        evidence.scan_status = ScanStatus.CLEAN
        evidence.save(update_fields=["scan_status"])
    return evidence


class TestSubmitClaim:
    def test_a_claim_marks_the_page_as_pending(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()

        claim = _submit(provider, make_user())

        provider.refresh_from_db()
        assert claim.status == ClaimDecision.PENDING
        assert provider.claim_status == ClaimStatus.PENDING
        # Every claim gets its own token; a shared one would let any applicant
        # satisfy another company's verification.
        assert claim.website_verification_token

    def test_an_unverified_email_cannot_claim(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        # The decision has to be deliverable somewhere.
        with pytest.raises(services.ClaimError):
            _submit(make_provider(), make_user(verified=False))

    def test_a_second_open_claim_is_refused(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()
        _submit(provider, make_user())

        with pytest.raises(services.ClaimError):
            _submit(provider, make_user())

    def test_a_claimed_page_cannot_be_claimed_again(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider(claim_status=ClaimStatus.CLAIMED)

        with pytest.raises(services.ClaimError):
            _submit(provider, make_user())


class TestEvidence:
    def test_a_stored_file_starts_unreadable(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        claim = _submit(make_provider(), make_user())

        evidence = _attach(claim, make_upload())

        # Nothing has scanned it yet, so nothing may open it - including the
        # person who uploaded it.
        assert evidence.scan_status == ScanStatus.PENDING
        assert evidence.is_readable is False
        assert evidence.sha256

    def test_a_file_that_is_not_what_it_claims_is_rejected(
        self,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        # The extension and the browser's Content-Type both say PDF; the bytes
        # do not, and the bytes are the only part the uploader cannot fake.
        with pytest.raises(ValidationError):
            inspect_upload(make_upload(content=b"MZ\x90\x00 this is an executable"))

    def test_an_oversized_file_is_rejected(
        self,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        with pytest.raises(ValidationError):
            inspect_upload(make_upload(content=b"%PDF-1.4" + b"0" * MAX_UPLOAD_BYTES))

    def test_the_default_scanner_leaves_the_file_pending(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        # No scanner configured in tests: the fail-closed default is the whole
        # point, so this asserts the absence of a scanner never reads as clean.
        claim = _submit(make_provider(), make_user())
        evidence = _attach(claim, make_upload())

        services.scan_evidence(evidence)

        evidence.refresh_from_db()
        assert evidence.scan_status == ScanStatus.PENDING
        assert evidence.is_readable is False

    def test_a_clean_verdict_makes_the_file_readable(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        claim = _submit(make_provider(), make_user())
        evidence = _attach(claim, make_upload())

        class CleanScanner:
            def scan(self, chunks: object) -> ScanResult:
                return ScanResult(status=ScanStatus.CLEAN, detail="OK", scanner="fake")

        monkeypatch.setattr(services, "get_scanner", CleanScanner)
        services.scan_evidence(evidence)

        evidence.refresh_from_db()
        assert evidence.is_readable is True

    def test_only_a_moderator_may_release_an_unscanned_file(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        applicant = make_user()
        claim = _submit(make_provider(), applicant)
        evidence = _attach(claim, make_upload())

        with pytest.raises(services.ClaimError):
            services.override_scan(evidence=evidence, reviewer=applicant, reason="please")

    def test_an_override_needs_a_reason_and_names_who_gave_it(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        claim = _submit(make_provider(), make_user())
        evidence = _attach(claim, make_upload())

        with pytest.raises(services.ClaimError):
            services.override_scan(evidence=evidence, reviewer=moderator, reason="   ")

        services.override_scan(
            evidence=evidence, reviewer=moderator, reason="No scanner deployed yet"
        )

        evidence.refresh_from_db()
        assert evidence.scan_status == ScanStatus.SKIPPED
        assert evidence.scan_override_by_id == moderator.pk
        assert evidence.is_readable is True

    def test_an_infected_file_can_never_be_released(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        claim = _submit(make_provider(), make_user())
        evidence = _attach(claim, make_upload())
        evidence.scan_status = ScanStatus.INFECTED
        evidence.save(update_fields=["scan_status"])

        with pytest.raises(services.ClaimError):
            services.override_scan(evidence=evidence, reviewer=moderator, reason="I trust it")


class TestWebsiteVerification:
    def test_a_match_is_recorded_with_the_method_that_found_it(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from apps.providers import verification

        claim = _submit(make_provider(), make_user())
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

        services.verify_claim_website(claim)

        claim.refresh_from_db()
        assert claim.is_website_verified
        assert claim.website_verification_method == verification.METHOD_DNS_TXT

    def test_a_failure_is_recorded_too(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from apps.providers import verification

        claim = _submit(make_provider(), make_user())
        monkeypatch.setattr(
            services,
            "verify_website",
            lambda website, token: verification.VerificationOutcome(
                verified=False,
                method="",
                attempts=(
                    verification.VerificationAttempt(
                        verification.METHOD_DNS_TXT, False, "No matching TXT record"
                    ),
                ),
            ),
        )

        services.verify_claim_website(claim)

        claim.refresh_from_db()
        # The trail is what the applicant fixes their DNS from, and what the
        # moderator reads instead of trusting a bare "verified" flag.
        assert claim.is_website_verified is False
        assert claim.website_verification_log[0]["detail"] == "No matching TXT record"


class TestDecisions:
    def test_approval_grants_membership_and_the_licence_badge(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        provider = make_provider()
        applicant = make_user()
        claim = _submit(provider, applicant)
        _attach(claim, make_upload(), clean=True)

        services.approve_claim(claim=claim, reviewer=moderator, reason="BR matches the register")

        provider.refresh_from_db()
        applicant.refresh_from_db()
        assert provider.claim_status == ClaimStatus.CLAIMED
        assert ProviderMember.objects.filter(user=applicant, provider=provider, is_active=True)
        assert applicant.role == Role.PROVIDER_MEMBER
        badge = provider.certifications.get(type=CertificationType.TCSP_LICENCE)
        # Traceable back to the decision that granted it, with no invented
        # expiry date on an official fact.
        assert badge.evidence_ref == f"claim:{claim.pk}"
        assert badge.expires_at is None

    def test_approval_is_refused_while_a_file_is_unreadable(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        claim = _submit(make_provider(), make_user())
        _attach(claim, make_upload())

        with pytest.raises(services.ClaimError):
            services.approve_claim(claim=claim, reviewer=moderator, reason="looks fine")

    def test_a_decision_always_carries_a_reason(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
    ) -> None:
        claim = _submit(make_provider(), make_user())

        with pytest.raises(services.ClaimError):
            services.approve_claim(claim=claim, reviewer=moderator, reason="  ")
        with pytest.raises(services.ClaimError):
            services.reject_claim(claim=claim, reviewer=moderator, reason="")

    def test_a_non_moderator_cannot_decide(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        applicant = make_user()
        claim = _submit(make_provider(), applicant)

        with pytest.raises(services.ClaimError):
            services.approve_claim(claim=claim, reviewer=applicant, reason="mine")

    def test_rejection_returns_the_page_and_starts_the_retention_clock(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        provider = make_provider()
        claim = _submit(provider, make_user())
        evidence = _attach(claim, make_upload())

        services.reject_claim(claim=claim, reviewer=moderator, reason="Name does not match")

        provider.refresh_from_db()
        evidence.refresh_from_db()
        assert provider.claim_status == ClaimStatus.REJECTED
        # COMPLIANCE section 4: the clock starts at the decision, not at upload.
        assert evidence.purge_at is not None

    def test_the_applicant_is_told_the_decision_and_the_reason(
        self,
        django_capture_on_commit_callbacks: Callable[..., Any],
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        """The reason is mandatory precisely because someone is owed it. Leaving
        it on a dashboard page the applicant has to think to revisit makes the
        requirement decorative."""
        applicant = make_user(email="applicant@example.com")
        claim = _submit(make_provider(), applicant)
        _attach(claim, make_upload(), clean=True)

        with django_capture_on_commit_callbacks(execute=True):
            services.approve_claim(
                claim=claim, reviewer=moderator, reason="BR matches the register"
            )

        [message] = mail.outbox
        assert message.to == ["applicant@example.com"]
        assert "BR matches the register" in message.body

    def test_a_refused_applicant_is_told_why_and_nobody_else_is_told_at_all(
        self,
        django_capture_on_commit_callbacks: Callable[..., Any],
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
    ) -> None:
        """A refused claim proves nothing about who the applicant is, so the
        company's real members must not learn that a stranger applied."""
        provider = make_provider()
        member = make_user(email="owner@example.com")
        ProviderMember.objects.create(user=member, provider=provider)
        claim = _submit(provider, make_user(email="stranger@example.com"))

        with django_capture_on_commit_callbacks(execute=True):
            services.reject_claim(claim=claim, reviewer=moderator, reason="Name does not match")

        [message] = mail.outbox
        assert message.to == ["stranger@example.com"]
        assert "Name does not match" in message.body

    def test_only_the_applicant_may_withdraw(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = make_provider()
        claim = _submit(provider, make_user())

        with pytest.raises(services.ClaimError):
            services.withdraw_claim(claim=claim, user=make_user())

        services.withdraw_claim(claim=claim, user=claim.submitted_by)

        provider.refresh_from_db()
        assert claim.status == ClaimDecision.WITHDRAWN
        # Withdrawn, not rejected: nobody judged this company.
        assert provider.claim_status == ClaimStatus.UNCLAIMED

    def test_a_decided_claim_cannot_be_decided_again(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
    ) -> None:
        claim = _submit(make_provider(), make_user())
        services.reject_claim(claim=claim, reviewer=moderator, reason="Not the licensee")

        with pytest.raises(services.ClaimError):
            services.approve_claim(claim=claim, reviewer=moderator, reason="changed my mind")


class TestRetention:
    def test_the_purge_deletes_the_bytes_and_keeps_the_record(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        claim = _submit(make_provider(), make_user())
        evidence = _attach(claim, make_upload())
        services.reject_claim(claim=claim, reviewer=moderator, reason="Not the licensee")

        purged = services.purge_expired_evidence(now=timezone.now() + timedelta(days=91))

        evidence.refresh_from_db()
        assert purged == 1
        assert not evidence.file
        # What an audit needs survives; what the platform must not keep does not.
        assert evidence.sha256
        assert evidence.purged_at is not None
        assert evidence.is_readable is False

    def test_evidence_inside_the_window_is_left_alone(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        claim = _submit(make_provider(), make_user())
        evidence = _attach(claim, make_upload())
        services.reject_claim(claim=claim, reviewer=moderator, reason="Not the licensee")

        assert services.purge_expired_evidence(now=timezone.now() + timedelta(days=1)) == 0

        evidence.refresh_from_db()
        assert evidence.purged_at is None
