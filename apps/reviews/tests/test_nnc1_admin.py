"""The NNC1 moderator queue.

A customised Django admin, so these cover only what is actually customised: the
register comparison, the mandatory reason, and the refusal to offer a link to a
file no scanner cleared.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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

CHANGELIST = "admin:reviews_nnc1verification_changelist"


@pytest.fixture
def staff_moderator(moderator: User) -> User:
    moderator.is_staff = True
    moderator.is_superuser = True
    moderator.save()
    return moderator


@pytest.fixture
def verification(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
    make_upload: Callable[..., SimpleUploadedFile],
) -> Nnc1Verification:
    provider = make_provider(licensee_kwargs={"name_en": "Comparable Secretarial Limited"})
    review = make_review(
        provider=provider,
        author=make_user(),
        overall="4.5",
        status=ReviewStatus.PUBLISHED,
        is_verified=False,
    )
    upload = make_upload()
    return services.submit_nnc1(
        review=review,
        uploader=review.author,
        upload=upload,
        inspected=inspect_upload(upload),
        declared_company_name="Buyer Holdings Limited",
        declared_secretary_name="Comparable Secretarial Ltd",
    )


def test_the_change_page_shows_the_register_beside_the_declaration(
    client: Client, staff_moderator: User, verification: Nnc1Verification
) -> None:
    """The comparison a moderator judges the document against, plus the warning
    that agreement in this table proves nothing (see matching.py)."""
    client.force_login(staff_moderator)

    response = client.get(reverse("admin:reviews_nnc1verification_change", args=[verification.pk]))

    content = response.content.decode()
    assert response.status_code == 200
    assert "Comparable Secretarial Limited" in content
    assert "Comparable Secretarial Ltd" in content
    assert "proves nothing" in content


def test_an_uncleared_document_is_not_linked(
    client: Client, staff_moderator: User, verification: Nnc1Verification
) -> None:
    """An unscanned file is not opened in a moderator's browser either."""
    client.force_login(staff_moderator)

    response = client.get(reverse("admin:reviews_nnc1verification_change", args=[verification.pk]))

    content = response.content.decode()
    assert reverse("reviews:nnc1_document", args=[verification.pk]) not in content
    assert "not cleared by a scanner" in content


def test_passing_asks_for_a_reason_before_anything_changes(
    client: Client, staff_moderator: User, verification: Nnc1Verification
) -> None:
    verification.scan_status = ScanStatus.CLEAN
    verification.save(update_fields=["scan_status"])
    client.force_login(staff_moderator)

    # First post: the selection only. The intermediate page comes back and the
    # review is untouched.
    response = client.post(
        reverse(CHANGELIST),
        {"action": "pass_verifications", "_selected_action": [str(verification.pk)]},
    )
    verification.refresh_from_db()
    assert response.status_code == 200
    assert verification.result == VerificationResult.NEEDS_HUMAN

    # Second post: with the reason.
    client.post(
        reverse(CHANGELIST),
        {
            "action": "pass_verifications",
            "_selected_action": [str(verification.pk)],
            "apply_reason": "1",
            "reason": "NNC1 names this company as first secretary",
        },
    )

    verification.refresh_from_db()
    verification.review.refresh_from_db()
    assert verification.result == VerificationResult.PASSED
    assert verification.reviewed_by_id == staff_moderator.pk
    assert verification.review_note == "NNC1 names this company as first secretary"
    assert verification.review.is_verified


def test_the_queue_refuses_to_pass_an_unscanned_document(
    client: Client, staff_moderator: User, verification: Nnc1Verification
) -> None:
    """The rule lives in services, so coming through the admin cannot bypass
    it - the moderator gets an error message and the row stays undecided."""
    client.force_login(staff_moderator)

    client.post(
        reverse(CHANGELIST),
        {
            "action": "pass_verifications",
            "_selected_action": [str(verification.pk)],
            "apply_reason": "1",
            "reason": "Looks plausible",
        },
    )

    verification.refresh_from_db()
    assert verification.result == VerificationResult.NEEDS_HUMAN
    assert not verification.review.is_verified


def test_failing_needs_no_readable_file(
    client: Client, staff_moderator: User, verification: Nnc1Verification
) -> None:
    client.force_login(staff_moderator)

    client.post(
        reverse(CHANGELIST),
        {
            "action": "fail_verifications",
            "_selected_action": [str(verification.pk)],
            "apply_reason": "1",
            "reason": "Declared secretary is a different licensee",
        },
    )

    verification.refresh_from_db()
    assert verification.result == VerificationResult.FAILED


def test_staff_cannot_type_in_a_verification(
    client: Client, staff_moderator: User, verification: Nnc1Verification
) -> None:
    """NNC1s arrive from reviewers, through the upload form."""
    client.force_login(staff_moderator)

    response = client.get(reverse("admin:reviews_nnc1verification_add"))

    assert response.status_code == 403
