"""Task orchestration, including the two places the reviews app hands over.

The point of these is not the agents - it is that a missing row, an unreadable
file or a failing model call all end as a returned string rather than as a
retry storm against something that will never succeed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from apps.agents.models import AgentRun
from apps.agents.tasks import analyse_quote, extract_nnc1, moderate_review
from apps.core.scanning import ScanResult, ScanStatus
from apps.core.uploads import inspect_upload
from apps.reviews import services as review_services
from apps.reviews.models import ReviewStatus
from apps.reviews.tasks import process_nnc1
from apps.rfq import services as rfq_services

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Review
    from apps.rfq.models import Rfq

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def rules_only(settings: Any) -> None:
    """These tests are about wiring, so the model stays switched off."""
    settings.AGENTS_ENABLED = False


def test_moderating_a_vanished_review_is_not_an_error() -> None:
    """A review deleted between submission and the worker picking it up."""
    assert moderate_review("01a00000-0000-7000-8000-000000000000") == "missing"
    assert AgentRun.objects.count() == 0


def test_moderating_reports_that_the_rules_answered(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    review = make_review(
        provider=make_provider(),
        author=make_user(email="buyer@example.com"),
        status=ReviewStatus.PENDING_MODERATION,
        is_verified=False,
    )

    assert moderate_review(str(review.pk)) == "fallback"


def test_analysing_a_vanished_quote_is_not_an_error() -> None:
    """Withdrawn and deleted between submission and the worker. A retry storm
    against a row that will never come back helps nobody."""
    assert analyse_quote("01a00000-0000-7000-8000-000000000000") == "missing"
    assert AgentRun.objects.count() == 0


def test_analysing_reports_that_the_rules_answered(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    provider, member = make_quoting_provider()
    quote = rfq_services.submit_quote(
        rfq=open_rfq, provider=provider, submitted_by=member, first_year_total_minor=6_800_00
    )

    assert analyse_quote(str(quote.pk)) == "fallback"


def test_a_quote_in_another_currency_is_reported_as_skipped(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    provider, member = make_quoting_provider()
    quote = rfq_services.submit_quote(
        rfq=open_rfq,
        provider=provider,
        submitted_by=member,
        first_year_total_minor=6_800_00,
        currency="CNY",
    )

    assert analyse_quote(str(quote.pk)) == "skipped"
    assert AgentRun.objects.count() == 0


def test_submitting_a_quote_queues_the_analysis(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    django_capture_on_commit_callbacks: Any,
) -> None:
    """Dispatched from the service on commit, so every path that accepts a
    quote gets the analysis and none of them can beat the row to the worker."""
    provider, member = make_quoting_provider()

    with django_capture_on_commit_callbacks(execute=True):
        quote = rfq_services.submit_quote(
            rfq=open_rfq, provider=provider, submitted_by=member, first_year_total_minor=6_800_00
        )

    quote.refresh_from_db()
    assert quote.analysis["used_fallback"] is True


def test_reading_a_vanished_verification_is_not_an_error() -> None:
    assert extract_nnc1("01a00000-0000-7000-8000-000000000000") == "missing"


def test_processing_an_unscannable_upload_does_not_reach_extraction(
    monkeypatch: pytest.MonkeyPatch,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
    make_upload: Callable[..., Any],
) -> None:
    """The default scanner is unavailable, so the file stays unreadable - and an
    unreadable file is not opened to be sent to a model either."""
    review = make_review(
        provider=make_provider(),
        author=make_user(email="buyer@example.com"),
        status=ReviewStatus.PENDING_MODERATION,
        is_verified=False,
    )
    upload = make_upload("nnc1.pdf")
    verification = review_services.submit_nnc1(
        review=review,
        uploader=review.author,
        upload=upload,
        inspected=inspect_upload(upload),
        declared_company_name="Buyer Holdings Limited",
        declared_secretary_name=review.provider.licensee.name_en,
    )

    process_nnc1(str(verification.pk))

    verification.refresh_from_db()
    assert not verification.is_readable
    assert AgentRun.objects.filter(agent_name="nnc1_extraction").count() == 0


def test_a_clean_scan_hands_the_document_to_extraction(
    monkeypatch: pytest.MonkeyPatch,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
    make_upload: Callable[..., Any],
) -> None:
    review = make_review(
        provider=make_provider(),
        author=make_user(email="buyer@example.com"),
        status=ReviewStatus.PENDING_MODERATION,
        is_verified=False,
    )
    upload = make_upload("nnc1.pdf")
    verification = review_services.submit_nnc1(
        review=review,
        uploader=review.author,
        upload=upload,
        inspected=inspect_upload(upload),
        declared_company_name="Buyer Holdings Limited",
        declared_secretary_name=review.provider.licensee.name_en,
    )
    monkeypatch.setattr(
        review_services,
        "get_scanner",
        lambda: type(
            "CleanScanner",
            (),
            {
                "name": "test",
                "scan": lambda self, handle: ScanResult(status=ScanStatus.CLEAN, detail="ok"),
            },
        )(),
    )

    process_nnc1(str(verification.pk))

    verification.refresh_from_db()
    assert verification.is_readable
    # Ran, fell back to rules, and wrote nothing but the advisory columns.
    run = AgentRun.objects.get(agent_name="nnc1_extraction")
    assert run.object_id == str(verification.pk)
    assert verification.extracted["used_fallback"] is True
