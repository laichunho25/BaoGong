"""Where agent output meets the database.

CLAUDE.md rule 3 is the whole subject: these tests exist to fail loudly if an
agent ever starts changing a review's status, a company's rating, or a
verification's outcome.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from apps.agents import selectors, services
from apps.agents.models import AgentRun, FeedbackVerdict
from apps.agents.tests.conftest import FakeBlock, FakeResponse
from apps.core.scanning import ScanStatus
from apps.core.uploads import inspect_upload
from apps.reviews import services as review_services
from apps.reviews.models import ReviewStatus, VerificationResult

if TYPE_CHECKING:
    from collections.abc import Callable

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
    provider = make_provider()
    return make_review(
        provider=provider,
        author=make_user(email="buyer@example.com"),
        status=ReviewStatus.PENDING_MODERATION,
        is_verified=False,
        body="Ring Amy on 9123 4567. They filed the incorporation in four days.",
    )


# ------------------------------------------------------------------- moderation


def test_moderating_a_review_writes_advice_and_leaves_the_review_pending(
    review: Review, enabled: None, fake_client: Callable[..., Any], moderation_payload: dict
) -> None:
    fake_client(FakeResponse([FakeBlock(moderation_payload)]))

    services.moderate_review(review)

    review.refresh_from_db()
    assert review.status == ReviewStatus.PENDING_MODERATION
    assert review.moderated_by is None
    assert review.moderation["severity"] == "low"
    assert review.moderation["run_id"] == str(AgentRun.objects.get().pk)
    assert review.moderation["escalation_reason"] == "routine"


def test_the_advice_records_that_the_rules_wrote_it(review: Review, settings: Any) -> None:
    """A moderator reading the queue has to be able to tell a model's opinion
    from a keyword match, because the two deserve different amounts of trust."""
    settings.AGENTS_ENABLED = False

    services.moderate_review(review)

    review.refresh_from_db()
    assert review.moderation["used_fallback"] is True
    assert review.moderation["fallback_reason"] == "disabled"
    # The rules caught the phone number, and a leak outranks "nobody read it".
    assert "personal_data_leak" in review.moderation["labels"]
    assert review.moderation["escalation_reason"] == "personal_data_leak"


def test_a_review_that_was_never_public_stays_that_way(review: Review, settings: Any) -> None:
    settings.AGENTS_ENABLED = False

    services.moderate_review(review)

    review.refresh_from_db()
    assert review.published_at is None
    assert review.is_verified is False


def test_the_run_is_linked_back_to_the_review(review: Review, settings: Any) -> None:
    settings.AGENTS_ENABLED = False

    services.moderate_review(review)

    run = selectors.latest_run_for(review)
    assert run is not None
    assert run.object_type == "reviews.Review"
    assert run.object_id == str(review.pk)


def test_submitting_a_review_queues_the_moderation_agent(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    settings: Any,
    django_capture_on_commit_callbacks: Any,
) -> None:
    """Dispatched from the service, so every path that creates a review gets it.

    ``on_commit`` because the worker must not arrive before the row exists;
    the capture fixture is what makes that observable inside a test transaction.
    """
    from decimal import Decimal

    settings.AGENTS_ENABLED = False
    provider = make_provider()

    with django_capture_on_commit_callbacks(execute=True):
        review = review_services.submit_review(
            provider=provider,
            author=make_user(email="someone@example.com"),
            body="They were slow to answer email but the filing itself was correct.",
            scores=dict.fromkeys(
                ("price_transparency", "responsiveness", "professionalism", "after_sales"),
                Decimal("4.0"),
            ),
        )

    review.refresh_from_db()
    assert review.moderation != {}
    assert review.status == ReviewStatus.PENDING_MODERATION


# ------------------------------------------------------------------- extraction


@pytest.fixture
def verification(
    review: Review, make_user: Callable[..., User], make_upload: Callable[..., Any]
) -> Any:
    upload = make_upload("nnc1.pdf")
    return review_services.submit_nnc1(
        review=review,
        uploader=review.author,
        upload=upload,
        inspected=inspect_upload(upload),
        declared_company_name="Buyer Holdings Limited",
        declared_secretary_name=review.provider.licensee.name_en,
    )


def test_an_unscanned_document_is_never_opened(verification: Any, settings: Any) -> None:
    """Not even to send it somewhere else. Same rule as showing it to a person."""
    settings.AGENTS_ENABLED = False

    assert services.extract_nnc1(verification) is None
    assert AgentRun.objects.count() == 0


def test_extraction_writes_only_the_advisory_columns(verification: Any, settings: Any) -> None:
    settings.AGENTS_ENABLED = False
    verification.scan_status = ScanStatus.CLEAN
    verification.save(update_fields=["scan_status"])

    services.extract_nnc1(verification)

    verification.refresh_from_db()
    assert verification.extracted["used_fallback"] is True
    assert verification.extraction_confidence == 0
    assert verification.agent_run_id_ref is not None
    # AI_AGENTS A3's red line: the outcome is untouched.
    assert verification.result == VerificationResult.NEEDS_HUMAN
    assert verification.reviewed_by is None


def test_a_decided_verification_is_not_read_again(
    verification: Any, settings: Any, moderator: User
) -> None:
    settings.AGENTS_ENABLED = False
    verification.scan_status = ScanStatus.CLEAN
    verification.save(update_fields=["scan_status"])
    review_services.decide_verification(
        verification=verification,
        reviewer=moderator,
        passed=True,
        note="Secretary on the form matches the licensee.",
    )

    assert services.extract_nnc1(verification) is None


# --------------------------------------------------------------------- feedback


def test_feedback_is_one_row_per_reviewer(review: Review, moderator: User, settings: Any) -> None:
    settings.AGENTS_ENABLED = False
    services.moderate_review(review)
    run = AgentRun.objects.get()

    services.record_feedback(agent_run=run, reviewer=moderator, verdict=FeedbackVerdict.WRONG)
    services.record_feedback(
        agent_run=run,
        reviewer=moderator,
        verdict=FeedbackVerdict.CORRECT,
        notes="On reflection the label was right.",
    )

    assert run.feedback.count() == 1
    assert run.feedback.get().verdict == FeedbackVerdict.CORRECT


def test_an_unknown_verdict_is_refused(review: Review, moderator: User, settings: Any) -> None:
    settings.AGENTS_ENABLED = False
    services.moderate_review(review)

    with pytest.raises(services.AgentServiceError):
        services.record_feedback(
            agent_run=AgentRun.objects.get(), reviewer=moderator, verdict="excellent"
        )
