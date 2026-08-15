"""The upload page and the document download.

The download view is the one place where an NNC1's bytes can leave the system,
so most of these tests are about who is refused and why the refusal is a 404.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from django.urls import reverse

from apps.core.scanning import ScanStatus
from apps.core.uploads import inspect_upload
from apps.reviews import services
from apps.reviews.models import Nnc1Verification, ReviewStatus, VerificationResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Review

pytestmark = pytest.mark.django_db


@pytest.fixture
def review(
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


def _stored(review: Review, upload: SimpleUploadedFile, *, clean: bool = False) -> Nnc1Verification:
    verification = services.submit_nnc1(
        review=review,
        uploader=review.author,
        upload=upload,
        inspected=inspect_upload(upload),
        declared_secretary_name=review.provider.licensee.name_en,
    )
    if clean:
        verification.scan_status = ScanStatus.CLEAN
        verification.save(update_fields=["scan_status"])
    return verification


class TestUploadPage:
    def test_the_page_states_what_happens_to_the_document(
        self, client: Client, review: Review
    ) -> None:
        """Retention and privacy are said before the file input, not in a
        policy page nobody opens (COMPLIANCE section 4)."""
        client.force_login(review.author)

        response = client.get(reverse("reviews:nnc1_upload", args=[review.pk]))

        body = response.content.decode()
        assert response.status_code == 200
        assert "90" in body
        assert "私有存储" in body

    def test_a_stranger_gets_a_404_rather_than_a_403(
        self, client: Client, review: Review, make_user: Callable[..., User]
    ) -> None:
        """Whether a review exists is not something an unrelated account gets to
        learn by probing URLs."""
        client.force_login(make_user())

        response = client.get(reverse("reviews:nnc1_upload", args=[review.pk]))

        assert response.status_code == 404

    def test_uploading_stores_the_document_and_leaves_the_review_alone(
        self, client: Client, review: Review, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        client.force_login(review.author)

        response = client.post(
            reverse("reviews:nnc1_upload", args=[review.pk]),
            {
                "document": make_upload(),
                "declared_company_name": "Buyer Holdings Limited",
                "declared_company_no": "3141592",
                "declared_secretary_name": review.provider.licensee.name_en,
            },
        )

        review.refresh_from_db()
        verification = Nnc1Verification.objects.get(review=review)
        assert response.status_code == 302
        assert verification.result == VerificationResult.NEEDS_HUMAN
        assert not review.is_verified

    def test_a_decided_review_cannot_be_re_uploaded_through_the_page(
        self,
        client: Client,
        review: Review,
        moderator: User,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        verification = _stored(review, make_upload(), clean=True)
        services.decide_verification(
            verification=verification, reviewer=moderator, passed=True, note="Matches"
        )
        client.force_login(review.author)

        response = client.get(reverse("reviews:nnc1_upload", args=[review.pk]))

        assert response.status_code == 302

    def test_a_file_that_is_not_what_it_claims_is_rejected_on_the_form(
        self, client: Client, review: Review, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        """Sniffed by content, not by extension, and reported next to the field
        rather than as a 500 from the service layer."""
        client.force_login(review.author)

        response = client.post(
            reverse("reviews:nnc1_upload", args=[review.pk]),
            {
                "document": make_upload(name="nnc1.pdf", content=b"MZ\x90\x00 not a pdf"),
                "declared_company_name": "Buyer Holdings Limited",
                "declared_secretary_name": review.provider.licensee.name_en,
            },
        )

        assert response.status_code == 200
        assert not Nnc1Verification.objects.filter(review=review).exists()


class TestDownload:
    def test_a_stranger_cannot_read_someone_elses_nnc1(
        self,
        client: Client,
        review: Review,
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        verification = _stored(review, make_upload(), clean=True)
        client.force_login(make_user())

        response = client.get(reverse("reviews:nnc1_document", args=[verification.pk]))

        assert response.status_code == 404

    def test_a_moderator_can_read_a_cleared_document(
        self,
        client: Client,
        review: Review,
        moderator: User,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        verification = _stored(review, make_upload(), clean=True)
        client.force_login(moderator)

        response = client.get(reverse("reviews:nnc1_document", args=[verification.pk]))

        assert response.status_code == 200
        # Personal data: no shared cache may keep a copy.
        assert response["Cache-Control"] == "private, no-store"

    def test_an_unscanned_document_is_refused_even_to_its_uploader(
        self, client: Client, review: Review, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        """Their own file is no safer to open than anyone else's, and it is
        their browser that would open it."""
        verification = _stored(review, make_upload())
        client.force_login(review.author)

        response = client.get(reverse("reviews:nnc1_document", args=[verification.pk]))

        assert response.status_code == 404

    def test_a_purged_document_is_gone_for_good(
        self,
        client: Client,
        review: Review,
        moderator: User,
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        verification = _stored(review, make_upload(), clean=True)
        services.decide_verification(
            verification=verification, reviewer=moderator, passed=True, note="Matches"
        )
        verification.file.delete(save=False)
        verification.purged_at = verification.reviewed_at
        verification.save(update_fields=["file", "purged_at"])
        client.force_login(moderator)

        response = client.get(reverse("reviews:nnc1_document", args=[verification.pk]))

        assert response.status_code == 404

    def test_an_unknown_id_is_a_404(self, client: Client, moderator: User) -> None:
        client.force_login(moderator)

        response = client.get(reverse("reviews:nnc1_document", args=[uuid4()]))

        assert response.status_code == 404


def test_my_reviews_offers_the_upload_to_an_unverified_review(
    client: Client, review: Review
) -> None:
    """An unverified review is published but weightless; saying "not counted"
    without saying what to do about it would be half the message."""
    client.force_login(review.author)

    response = client.get(reverse("reviews:my_reviews"))

    assert reverse("reviews:nnc1_upload", args=[review.pk]) in response.content.decode()
