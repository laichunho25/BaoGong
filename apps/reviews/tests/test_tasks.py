"""The recompute tasks.

Called eagerly (``CELERY_TASK_ALWAYS_EAGER`` in the test settings), so these are
really tests of the orchestration: that a task which arrives after the company
is gone logs and returns instead of retrying forever, and that the bulk rebuild
touches every company whose number could be wrong.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.core.scanning import ScanStatus
from apps.core.uploads import inspect_upload
from apps.reviews import services, tasks
from apps.reviews.matching import MatchMethod

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Nnc1Verification, Review

pytestmark = pytest.mark.django_db


def test_recompute_rating_refreshes_one_company(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    provider = make_provider()
    make_review(provider=provider, author=make_user(), overall="4.5")

    assert tasks.recompute_rating(str(provider.pk)) == "4.95"


def test_recompute_rating_tolerates_a_deleted_company(
    make_provider: Callable[..., Provider],
) -> None:
    """A verification result can land after the company row is gone; retrying
    that forever would block the queue behind a company that no longer exists."""
    provider = make_provider()
    provider_id = str(provider.pk)
    provider.delete()

    assert tasks.recompute_rating(provider_id) is None


def test_the_bulk_rebuild_covers_every_scored_company(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    """The formula is a product decision that will change; when it does, every
    cached number on the site is wrong until this runs."""
    first, second = make_provider(), make_provider()
    for provider in (first, second):
        make_review(provider=provider, author=make_user(), overall="4.5")
        tasks.recompute_rating(str(provider.pk))
    first.refresh_from_db()
    first.rating_cached = Decimal("1.00")
    first.save(update_fields=["rating_cached"])

    assert tasks.recompute_all_ratings() == 2

    first.refresh_from_db()
    assert first.rating_cached == Decimal("4.95")


def _uploaded(review: Review, upload: SimpleUploadedFile, secretary: str) -> Nnc1Verification:
    return services.submit_nnc1(
        review=review,
        uploader=review.author,
        upload=upload,
        inspected=inspect_upload(upload),
        declared_secretary_name=secretary,
    )


def test_processing_an_upload_scans_it_and_compares_the_name(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
    make_upload: Callable[..., SimpleUploadedFile],
) -> None:
    provider = make_provider()
    review = make_review(provider=provider, author=make_user(), overall="4.5", is_verified=False)
    verification = _uploaded(review, make_upload(), provider.licensee.name_en)

    # No scanner is configured in tests, so the fail-closed default stands.
    assert tasks.process_nnc1(str(verification.pk)) == ScanStatus.PENDING

    verification.refresh_from_db()
    review.refresh_from_db()
    assert verification.match_method == MatchMethod.EXACT
    # And still nothing about the review has changed: CLAUDE.md rule 3 in the
    # rule-based case as well - the match is evidence for a moderator.
    assert not review.is_verified


def test_the_name_match_runs_even_when_the_scan_does_not_clear_the_file(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
    make_upload: Callable[..., SimpleUploadedFile],
) -> None:
    """It reads columns the uploader typed rather than the file, so it costs
    nothing and gives a moderator looking at a quarantined upload something to
    go on."""
    provider = make_provider()
    review = make_review(provider=provider, author=make_user(), overall="4.5", is_verified=False)
    verification = _uploaded(review, make_upload(), "Unlicensed Backroom Agency")

    tasks.process_nnc1(str(verification.pk))

    verification.refresh_from_db()
    assert verification.scan_status == ScanStatus.PENDING
    assert verification.match_method == MatchMethod.NONE
    assert verification.match_detail


def test_processing_tolerates_a_deleted_upload() -> None:
    """Someone may withdraw a review between the upload and the worker picking
    it up; retrying that forever would block the queue."""
    assert tasks.process_nnc1(str(uuid4())) == "missing"


def test_the_daily_purge_deletes_expired_documents(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
    make_upload: Callable[..., SimpleUploadedFile],
    moderator: User,
) -> None:
    """The daily job whose silent failure would matter most (COMPLIANCE
    section 4): an NNC1 carries more personal data than anything else the
    platform accepts."""
    provider = make_provider()
    review = make_review(provider=provider, author=make_user(), overall="4.5", is_verified=False)
    verification = _uploaded(review, make_upload(), provider.licensee.name_en)
    verification.scan_status = ScanStatus.CLEAN
    verification.save(update_fields=["scan_status"])
    services.decide_verification(
        verification=verification, reviewer=moderator, passed=True, note="Matches"
    )
    verification.purge_at = timezone.now() - timedelta(seconds=1)
    verification.save(update_fields=["purge_at"])

    assert tasks.purge_nnc1_documents() == 1

    verification.refresh_from_db()
    assert not verification.file
    assert verification.sha256
