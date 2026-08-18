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
from apps.rfq import services as rfq_services

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Review
    from apps.rfq.models import Rfq

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


def test_the_prefill_writes_no_requirement(settings: Any) -> None:
    """A1's red line (AI_AGENTS A1, CLAUDE.md rule 3). What comes back is form
    values; the buyer confirms them, and ``rfq.services.create_rfq`` is the only
    thing that puts a requirement in front of a licensed company."""
    from apps.rfq.models import Rfq

    settings.AGENTS_ENABLED = False

    result = services.draft_rfq_prefill(raw_input="想注册香港公司，还要开户。")

    assert result.data is not None
    assert Rfq.objects.count() == 0
    # The run is still recorded: a fallback is a decision too (CLAUDE.md rule 4).
    assert AgentRun.objects.get().agent_name == "rfq_intake"


# ---------------------------------------------------------------------- matching


def test_matching_writes_only_the_suggestion_column(
    open_rfq: Rfq, make_provider: Callable[..., Provider], settings: Any
) -> None:
    """AI_AGENTS A2, CLAUDE.md rule 3. A shortlist is a reading list for the
    buyer: it gives no company standing on the requirement and moves nothing."""
    from apps.providers.models import ServiceCategory, ServiceOffering

    settings.AGENTS_ENABLED = False
    provider = make_provider(bank_account_support=True)
    ServiceOffering.objects.create(provider=provider, category=ServiceCategory.INCORPORATION)
    before_status, before_visibility = open_rfq.status, open_rfq.visibility

    result = services.match_providers(open_rfq)

    assert result is not None
    open_rfq.refresh_from_db()
    assert open_rfq.status == before_status
    assert open_rfq.visibility == before_visibility
    assert open_rfq.matches["used_fallback"] is True
    assert open_rfq.matches["items"][0]["provider_id"] == provider.slug
    assert open_rfq.matches["run_id"] == str(AgentRun.objects.get(agent_name="matching").pk)


def test_an_empty_pool_never_reaches_the_model(open_rfq: Rfq, settings: Any) -> None:
    """Nobody on the register can serve this. A model cannot improve that, and
    calling one to rank nothing spends money to produce an empty list."""
    settings.AGENTS_ENABLED = False

    assert services.match_providers(open_rfq) is None

    open_rfq.refresh_from_db()
    assert open_rfq.matches == {}
    assert not AgentRun.objects.filter(agent_name="matching").exists()


def test_the_candidate_summary_hides_what_a_company_paid_for(
    make_provider: Callable[..., Provider],
) -> None:
    """COMPLIANCE section 5. A model told which companies pay would have a fact
    it could offer the buyer as a reason to choose one."""
    from apps.providers.models import Tier

    provider = make_provider(tier=Tier.VERIFIED)

    summary = services.candidate_summary(provider, services=[])

    assert "tier" not in summary
    assert summary["provider_id"] == provider.slug


def test_the_candidate_summary_prices_only_what_was_asked_for(
    make_provider: Callable[..., Provider],
) -> None:
    from apps.providers.models import PriceItem, ServiceCategory, ServiceOffering

    provider = make_provider()
    wanted = ServiceOffering.objects.create(
        provider=provider, category=ServiceCategory.INCORPORATION
    )
    other = ServiceOffering.objects.create(provider=provider, category=ServiceCategory.TRADEMARK)
    PriceItem.objects.create(offering=wanted, label="from", amount_minor=4_800_00)
    PriceItem.objects.create(offering=other, label="from", amount_minor=100_00)

    summary = services.candidate_summary(provider, services=[ServiceCategory.INCORPORATION])

    assert summary["price_from_hkd"] == 4800


def test_a_quote_in_another_currency_is_not_analysed(
    open_rfq: Rfq, make_quoting_provider: Callable[..., tuple[Provider, User]], settings: Any
) -> None:
    """Every figure in A5's schema is HKD and so are the percentiles. Comparing
    across currencies needs a rate the platform has no business inventing, so
    the quote keeps its place in the table and carries no analysis."""
    settings.AGENTS_ENABLED = False
    provider, member = make_quoting_provider()
    quote = rfq_services.submit_quote(
        rfq=open_rfq,
        provider=provider,
        submitted_by=member,
        first_year_total_minor=9_800_00,
        currency="CNY",
    )

    assert services.analyse_quote(quote) is None
    quote.refresh_from_db()
    assert quote.analysis == {}


def test_the_analysis_lands_beside_the_price_and_changes_nothing_else(
    open_rfq: Rfq, make_quoting_provider: Callable[..., tuple[Provider, User]], settings: Any
) -> None:
    settings.AGENTS_ENABLED = False
    provider, member = make_quoting_provider()
    quote = rfq_services.submit_quote(
        rfq=open_rfq,
        provider=provider,
        submitted_by=member,
        first_year_total_minor=4_500_00,
        includes_govt_fee=False,
        line_items=[{"label": "incorporation_service", "amount_minor": 4_500_00}],
    )
    before = quote.status

    services.analyse_quote(quote)

    quote.refresh_from_db()
    assert quote.status == before
    assert quote.first_year_total_minor == 4_500_00
    assert "missing_govt_fee" in quote.analysis["flags"]
    assert quote.analysis["used_fallback"] is True
    assert quote.analysis["run_id"] == str(AgentRun.objects.get().pk)


def test_the_analysis_records_how_much_market_there_was(
    open_rfq: Rfq, make_quoting_provider: Callable[..., tuple[Provider, User]], settings: Any
) -> None:
    """A reader of the stored analysis has to be able to tell "cheaper than the
    market" from "cheaper than the four quotes we had that week"."""
    settings.AGENTS_ENABLED = False
    provider, member = make_quoting_provider()
    quote = rfq_services.submit_quote(
        rfq=open_rfq, provider=provider, submitted_by=member, first_year_total_minor=9_800_00
    )

    services.analyse_quote(quote)

    quote.refresh_from_db()
    # One quote on the platform, which is below the sample floor, so there is
    # no market figure to record and none is claimed.
    assert quote.analysis["percentile_sample"] == {}
    assert "below_market_p10" not in quote.analysis["flags"]


def test_an_unknown_verdict_is_refused(review: Review, moderator: User, settings: Any) -> None:
    settings.AGENTS_ENABLED = False
    services.moderate_review(review)

    with pytest.raises(services.AgentServiceError):
        services.record_feedback(
            agent_run=AgentRun.objects.get(), reviewer=moderator, verdict="excellent"
        )
