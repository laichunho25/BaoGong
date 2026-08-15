"""NNC1 upload, verification and retention.

These tests are written around the four promises the flow makes:

* the uploader's - the document is private, replaceable while undecided, and
  deleted 90 days after the decision;
* the reader's - a "已验证" badge was granted by a named person who wrote down
  why, and only that grant puts weight behind a score;
* the moderator's - nothing is passed on a file no scanner cleared;
* the platform's - a name match is evidence and never a gate.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone

from apps.core.scanning import ScanResult, ScanStatus
from apps.core.uploads import inspect_upload
from apps.reviews import selectors, services
from apps.reviews.matching import MatchMethod
from apps.reviews.models import Nnc1Verification, ReviewStatus, VerificationResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Review

pytestmark = pytest.mark.django_db


def _upload(
    review: Review,
    uploader: User,
    upload: SimpleUploadedFile,
    *,
    secretary: str = "",
    clean: bool = False,
) -> Nnc1Verification:
    verification = services.submit_nnc1(
        review=review,
        uploader=uploader,
        upload=upload,
        inspected=inspect_upload(upload),
        declared_company_name="Buyer Holdings Limited",
        declared_secretary_name=secretary or review.provider.licensee.name_en,
    )
    if clean:
        verification.scan_status = ScanStatus.CLEAN
        verification.save(update_fields=["scan_status"])
    return verification


@pytest.fixture
def unverified_review(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> Review:
    return make_review(
        provider=make_provider(),
        author=make_user(),
        overall="4.5",
        status=ReviewStatus.PUBLISHED,
        is_verified=False,
    )


class TestSubmitNnc1:
    def test_an_upload_changes_nothing_about_the_review_yet(
        self, unverified_review: Review, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        """Uploading is a request, not a verification: the badge and the score
        stay exactly where they were until a moderator acts."""
        verification = _upload(unverified_review, unverified_review.author, make_upload())

        unverified_review.refresh_from_db()
        assert verification.result == VerificationResult.NEEDS_HUMAN
        assert not unverified_review.is_verified
        # Unscanned means unreadable, including to the person who uploaded it.
        assert verification.scan_status == ScanStatus.PENDING
        assert not verification.is_readable

    def test_only_the_author_may_upload(
        self,
        unverified_review: Review,
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """The badge asserts that *this reviewer* was a client, so a document
        supplied by anyone else proves the wrong thing."""
        with pytest.raises(services.ReviewError):
            _upload(unverified_review, make_user(), make_upload())

    def test_the_file_is_fingerprinted(
        self, unverified_review: Review, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        """The hash outlives the document: after the purge it is the only proof
        that the badge was granted on the file that was actually submitted."""
        verification = _upload(unverified_review, unverified_review.author, make_upload())

        assert len(verification.sha256) == 64
        assert verification.size_bytes > 0

    def test_re_uploading_replaces_an_undecided_document(
        self, unverified_review: Review, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        """People photograph the wrong page. While nobody has decided, the
        correction should not need a support ticket."""
        first = _upload(unverified_review, unverified_review.author, make_upload())
        second = _upload(
            unverified_review, unverified_review.author, make_upload(name="nnc1-correct.pdf")
        )

        assert Nnc1Verification.objects.filter(review=unverified_review).count() == 1
        assert second.pk != first.pk

    def test_re_uploading_after_a_decision_is_refused(
        self,
        unverified_review: Review,
        moderator: User,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """Otherwise a passed verification could be swapped for a different
        document after the fact, and the hash on file would prove nothing."""
        verification = _upload(
            unverified_review, unverified_review.author, make_upload(), clean=True
        )
        services.decide_verification(
            verification=verification, reviewer=moderator, passed=True, note="NNC1 matches"
        )

        with pytest.raises(services.ReviewError):
            _upload(unverified_review, unverified_review.author, make_upload())


class TestScanning:
    def test_the_default_scanner_leaves_the_file_unreadable(
        self, unverified_review: Review, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        """No scanner is configured in tests, and that is the point: the absence
        of a scanner must never read as clean."""
        verification = _upload(unverified_review, unverified_review.author, make_upload())

        services.scan_nnc1(verification)

        verification.refresh_from_db()
        assert verification.scan_status == ScanStatus.PENDING
        assert not verification.is_readable

    def test_a_clean_verdict_makes_the_document_readable(
        self,
        unverified_review: Review,
        make_upload: Callable[..., SimpleUploadedFile],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class CleanScanner:
            def scan(self, chunks: object) -> ScanResult:
                return ScanResult(status=ScanStatus.CLEAN, detail="OK", scanner="fake")

        monkeypatch.setattr(services, "get_scanner", CleanScanner)
        verification = _upload(unverified_review, unverified_review.author, make_upload())

        services.scan_nnc1(verification)

        verification.refresh_from_db()
        assert verification.is_readable
        assert verification.scanned_at is not None


class TestNameMatch:
    def test_a_declaration_naming_the_company_under_review_is_recorded_as_matching(
        self, unverified_review: Review, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        verification = _upload(unverified_review, unverified_review.author, make_upload())

        services.run_name_match(verification)

        verification.refresh_from_db()
        assert verification.match_method == MatchMethod.EXACT
        assert verification.matched_licence_no == unverified_review.provider.licensee.licence_no

    def test_a_match_does_not_verify_anything_by_itself(
        self, unverified_review: Review, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        """The load-bearing test of the whole design: the declared name is typed
        by the person asking to be verified, so agreement proves only that they
        can read the register."""
        verification = _upload(unverified_review, unverified_review.author, make_upload())

        services.run_name_match(verification)

        unverified_review.refresh_from_db()
        verification.refresh_from_db()
        assert verification.match_method == MatchMethod.EXACT
        assert verification.result == VerificationResult.NEEDS_HUMAN
        assert not unverified_review.is_verified

    def test_a_declaration_naming_another_licensee_says_so(
        self,
        unverified_review: Review,
        make_licensee: Callable[..., object],
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """A moderator reading "this names a different licensed TCSP" and one
        reading "this names nobody licensed" go down very different paths, so
        the detail line distinguishes them."""
        make_licensee(name_en="Pearl River Corporate Services Limited")
        verification = _upload(
            unverified_review,
            unverified_review.author,
            make_upload(),
            secretary="Pearl River Corporate Services Ltd",
        )

        services.run_name_match(verification)

        verification.refresh_from_db()
        assert verification.match_method == MatchMethod.NONE
        assert "different licensee" in verification.match_detail

    def test_a_declaration_naming_nobody_says_that_instead(
        self, unverified_review: Review, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        verification = _upload(
            unverified_review,
            unverified_review.author,
            make_upload(),
            secretary="Unlicensed Backroom Agency",
        )

        services.run_name_match(verification)

        verification.refresh_from_db()
        assert "No licensed company" in verification.match_detail


class TestDecideVerification:
    def test_passing_grants_the_badge_and_moves_the_score(
        self,
        unverified_review: Review,
        moderator: User,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """Verification is what puts weight behind a review (RATING_SYSTEM
        section 2), so the company's score must move in the same transaction -
        a badge visible before the score it caused is a visible contradiction."""
        provider = unverified_review.provider
        verification = _upload(
            unverified_review, unverified_review.author, make_upload(), clean=True
        )

        services.decide_verification(
            verification=verification,
            reviewer=moderator,
            passed=True,
            note="NNC1 names this company as first secretary",
        )

        unverified_review.refresh_from_db()
        provider.refresh_from_db()
        assert unverified_review.is_verified
        assert str(provider.rating_cached) == "4.95"

    def test_failing_takes_the_badge_back_and_the_weight_with_it(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_review: Callable[..., Review],
        moderator: User,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """A verification granted in error has to be revocable, and revoking it
        must remove the review's weight rather than only its badge."""
        provider = make_provider()
        review = make_review(provider=provider, author=make_user(), overall="4.5", is_verified=True)
        services.recompute_provider_rating(str(provider.pk))
        verification = _upload(review, review.author, make_upload(), clean=True)

        services.decide_verification(
            verification=verification,
            reviewer=moderator,
            passed=False,
            note="Document names a different company",
        )

        review.refresh_from_db()
        provider.refresh_from_db()
        assert not review.is_verified
        # No verified reviews left: null, never the prior's 5.00.
        assert provider.rating_cached is None

    def test_only_a_moderator_may_decide(
        self, unverified_review: Review, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        """The badge is the platform's assertion, not a user's."""
        verification = _upload(
            unverified_review, unverified_review.author, make_upload(), clean=True
        )

        with pytest.raises(services.ReviewError):
            services.decide_verification(
                verification=verification,
                reviewer=unverified_review.author,
                passed=True,
                note="Trust me",
            )

    def test_a_decision_without_a_reason_is_refused(
        self,
        unverified_review: Review,
        moderator: User,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """The question "why is this company rated 4.9" has to be answerable
        months later, by someone who was not in the room."""
        verification = _upload(
            unverified_review, unverified_review.author, make_upload(), clean=True
        )

        with pytest.raises(services.ReviewError):
            services.decide_verification(
                verification=verification, reviewer=moderator, passed=True, note="   "
            )

    def test_an_unscanned_document_cannot_be_passed(
        self,
        unverified_review: Review,
        moderator: User,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """A badge backed by a file nobody could open is worse than no badge."""
        verification = _upload(unverified_review, unverified_review.author, make_upload())

        with pytest.raises(services.ReviewError):
            services.decide_verification(
                verification=verification, reviewer=moderator, passed=True, note="Looks fine"
            )

    def test_an_unscanned_document_can_still_be_failed(
        self,
        unverified_review: Review,
        moderator: User,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """Rejection needs no file: the declared secretary alone can be enough
        to close the case, and a quarantined upload must not be a stuck row."""
        verification = _upload(unverified_review, unverified_review.author, make_upload())

        services.decide_verification(
            verification=verification,
            reviewer=moderator,
            passed=False,
            note="Declared secretary is a different licensee",
        )

        verification.refresh_from_db()
        assert verification.result == VerificationResult.FAILED

    def test_the_retention_clock_starts_at_the_decision(
        self,
        unverified_review: Review,
        moderator: User,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """COMPLIANCE section 4 gives the document 90 days from the point it has
        done its job, not from the point it arrived."""
        verification = _upload(
            unverified_review, unverified_review.author, make_upload(), clean=True
        )
        assert verification.purge_at is None

        services.decide_verification(
            verification=verification, reviewer=moderator, passed=True, note="Matches"
        )

        verification.refresh_from_db()
        assert verification.purge_at is not None
        assert verification.reviewed_at is not None
        assert (verification.purge_at - verification.reviewed_at).days == 90


class TestRetention:
    def test_the_bytes_go_and_the_record_stays(
        self,
        unverified_review: Review,
        moderator: User,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """What must not be kept is the document; the record that a badge was
        granted on it is exactly what an audit asks for."""
        verification = _upload(
            unverified_review, unverified_review.author, make_upload(), clean=True
        )
        services.decide_verification(
            verification=verification, reviewer=moderator, passed=True, note="Matches"
        )

        purged = services.purge_expired_nnc1(now=timezone.now() + timedelta(days=91))

        verification.refresh_from_db()
        unverified_review.refresh_from_db()
        assert purged == 1
        assert not verification.file
        assert verification.purged_at is not None
        assert verification.sha256
        assert verification.review_note
        # A verification does not lapse with its document: re-verifying every
        # reviewer every 90 days is not a promise the platform can keep.
        assert unverified_review.is_verified
        assert not verification.is_readable

    def test_an_undecided_document_is_not_purged(
        self, unverified_review: Review, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        """The clock has not started; purging here would delete evidence out
        from under the moderator who is about to look at it."""
        _upload(unverified_review, unverified_review.author, make_upload())

        assert services.purge_expired_nnc1(now=timezone.now() + timedelta(days=365)) == 0

    def test_purging_twice_deletes_once(
        self,
        unverified_review: Review,
        moderator: User,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        verification = _upload(
            unverified_review, unverified_review.author, make_upload(), clean=True
        )
        services.decide_verification(
            verification=verification, reviewer=moderator, passed=True, note="Matches"
        )
        later = timezone.now() + timedelta(days=91)

        services.purge_expired_nnc1(now=later)

        assert services.purge_expired_nnc1(now=later) == 0


class TestSelectors:
    def test_the_queue_holds_only_undecided_uploads(
        self,
        unverified_review: Review,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_review: Callable[..., Review],
        moderator: User,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        waiting = _upload(unverified_review, unverified_review.author, make_upload())
        other = make_review(
            provider=make_provider(), author=make_user(), overall="4.0", is_verified=False
        )
        decided = _upload(other, other.author, make_upload(), clean=True)
        services.decide_verification(
            verification=decided, reviewer=moderator, passed=True, note="Matches"
        )

        assert list(selectors.verification_queue()) == [waiting]

    def test_verification_for_finds_the_one_upload(
        self, unverified_review: Review, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        assert selectors.verification_for(unverified_review) is None

        verification = _upload(unverified_review, unverified_review.author, make_upload())

        assert selectors.verification_for(unverified_review) == verification
