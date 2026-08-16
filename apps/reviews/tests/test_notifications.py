"""What the platform tells people about decisions it made concerning them.

Every test here is really the same test asked four times: the moderator had to
type a reason, so the person on the other end must receive that reason. A
decision communicated only by a change on a page the person has to think to
revisit is, for them, a decision made in silence.

The other half is what must **not** travel: mail passes through relays nobody
here controls, so the review text, the NNC1 fields and the uploaded filenames
stay behind a login (CLAUDE.md rule 5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.core import mail

from apps.accounts.models import ProviderMember
from apps.reviews import services
from apps.reviews.models import DisputeGround, ReviewStatus, VerificationResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Nnc1Verification, Review

pytestmark = pytest.mark.django_db

REASON = "Reads as a first-hand account of an engagement."


@pytest.fixture
def provider(make_provider: Callable[..., Provider]) -> Provider:
    return make_provider()


@pytest.fixture
def author(make_user: Callable[..., User]) -> User:
    return make_user(email="buyer@example.com")


@pytest.fixture
def member(make_user: Callable[..., User], provider: Provider) -> User:
    user = make_user(email="secretary@example.com")
    ProviderMember.objects.create(user=user, provider=provider)
    return user


@pytest.fixture
def pending(make_review: Callable[..., Review], provider: Provider, author: User) -> Review:
    return make_review(
        provider=provider,
        author=author,
        status=ReviewStatus.PENDING_MODERATION,
        is_verified=False,
    )


def _sent_to(address: str) -> list[mail.EmailMessage]:
    return [message for message in mail.outbox if address in message.to]


# --------------------------------------------------------------- moderation


def test_the_author_is_told_the_outcome_and_the_reason(
    django_capture_on_commit_callbacks: Callable[..., object],
    pending: Review,
    moderator: User,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        services.publish_review(review=pending, moderator=moderator, note=REASON)

    [message] = _sent_to("buyer@example.com")
    assert REASON in message.body
    assert "已发布" in message.subject


def test_a_hidden_review_is_explained_to_its_author(
    django_capture_on_commit_callbacks: Callable[..., object],
    make_review: Callable[..., Review],
    provider: Provider,
    author: User,
    moderator: User,
) -> None:
    """Being taken down without being told is how a platform loses the person
    who wrote the only first-hand account it had."""
    review = make_review(provider=provider, author=author)

    with django_capture_on_commit_callbacks(execute=True):
        services.hide_review(review=review, moderator=moderator, note="Names a third party.")

    [message] = _sent_to("buyer@example.com")
    assert "Names a third party." in message.body
    # Hidden is not deleted, and the mail has to say so or the author assumes
    # their text is gone (COMPLIANCE section 3).
    assert "并未删除" in message.body


def test_the_company_hears_about_a_review_when_it_is_published_not_when_it_is_written(
    django_capture_on_commit_callbacks: Callable[..., object],
    pending: Review,
    member: User,
    moderator: User,
) -> None:
    """A mail at submission time would hand a named business the identity of a
    complaining customer before anyone had checked the complaint."""
    assert pending.status == ReviewStatus.PENDING_MODERATION
    assert _sent_to("secretary@example.com") == []

    with django_capture_on_commit_callbacks(execute=True):
        services.publish_review(review=pending, moderator=moderator, note=REASON)

    [message] = _sent_to("secretary@example.com")
    assert "申诉" in message.body  # the right of appeal travels with the news
    assert "不会隐藏评价" in message.body


def test_the_company_is_not_told_about_a_review_that_was_never_published(
    django_capture_on_commit_callbacks: Callable[..., object],
    pending: Review,
    member: User,
    moderator: User,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        services.remove_review(review=pending, moderator=moderator, note="Spam.")

    assert _sent_to("secretary@example.com") == []


def test_a_restored_review_is_not_announced_as_new(
    django_capture_on_commit_callbacks: Callable[..., object],
    pending: Review,
    member: User,
    moderator: User,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        services.publish_review(review=pending, moderator=moderator, note=REASON)
        services.hide_review(review=pending, moderator=moderator, note="Checking a claim.")
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        services.publish_review(review=pending, moderator=moderator, note="Claim checked out.")

    assert _sent_to("secretary@example.com") == []
    assert len(_sent_to("buyer@example.com")) == 1


def test_the_review_text_never_leaves_the_platform_by_mail(
    django_capture_on_commit_callbacks: Callable[..., object],
    pending: Review,
    member: User,
    moderator: User,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        services.publish_review(review=pending, moderator=moderator, note=REASON)

    for message in mail.outbox:
        assert pending.body not in message.body


# --------------------------------------------------------------------- NNC1


@pytest.fixture
def verification(
    make_review: Callable[..., Review],
    make_upload: Callable[..., object],
    provider: Provider,
    author: User,
) -> Nnc1Verification:
    from apps.core.scanning import ScanStatus
    from apps.core.uploads import inspect_upload

    review = make_review(provider=provider, author=author, is_verified=False)
    upload = make_upload("nnc1.pdf")
    record = services.submit_nnc1(
        review=review,
        uploader=author,
        upload=upload,  # type: ignore[arg-type]
        inspected=inspect_upload(upload),  # type: ignore[arg-type]
        declared_company_name="Buyer Holdings Limited",
        declared_secretary_name="Some Secretaries Limited",
    )
    record.scan_status = ScanStatus.CLEAN
    record.save(update_fields=["scan_status"])
    return record


def test_the_uploader_is_told_the_verification_result(
    django_capture_on_commit_callbacks: Callable[..., object],
    verification: Nnc1Verification,
    moderator: User,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        services.decide_verification(
            verification=verification,
            reviewer=moderator,
            passed=True,
            note="NNC1 lists this company as secretary.",
        )

    [message] = _sent_to("buyer@example.com")
    assert "NNC1 lists this company as secretary." in message.body
    # The retention promise is repeated where the person can act on it.
    assert "天内删除" in message.body


def test_a_failed_verification_does_not_repeat_what_was_in_the_document(
    django_capture_on_commit_callbacks: Callable[..., object],
    verification: Nnc1Verification,
    moderator: User,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        services.decide_verification(
            verification=verification,
            reviewer=moderator,
            passed=False,
            note="The document names a different company.",
        )

    [message] = _sent_to("buyer@example.com")
    assert verification.result == VerificationResult.FAILED
    assert "Some Secretaries Limited" not in message.body
    assert "Buyer Holdings Limited" not in message.body


# ------------------------------------------------------------------ disputes


def test_the_company_gets_an_answer_to_its_dispute(
    django_capture_on_commit_callbacks: Callable[..., object],
    make_review: Callable[..., Review],
    provider: Provider,
    author: User,
    member: User,
    moderator: User,
) -> None:
    """COMPLIANCE section 3 promises the company an answer within five working
    days. An answer it is never sent is a deadline met on paper only."""
    review = make_review(provider=provider, author=author)
    dispute = services.raise_dispute(
        review=review,
        raised_by=member,
        ground=DisputeGround.NOT_A_CUSTOMER,
        reason="We hold no engagement record for this person in any year.",
    )
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        services.decide_dispute(
            dispute=dispute,
            moderator=moderator,
            decision="keep",
            note="The reviewer produced an NNC1 naming this company.",
        )

    [message] = _sent_to("secretary@example.com")
    assert "The reviewer produced an NNC1 naming this company." in message.body
    assert "维持原评价" in message.body


def test_upholding_a_dispute_tells_both_sides(
    django_capture_on_commit_callbacks: Callable[..., object],
    make_review: Callable[..., Review],
    provider: Provider,
    author: User,
    member: User,
    moderator: User,
) -> None:
    review = make_review(provider=provider, author=author)
    dispute = services.raise_dispute(
        review=review,
        raised_by=member,
        ground=DisputeGround.NOT_A_CUSTOMER,
        reason="We hold no engagement record for this person in any year.",
    )
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        services.decide_dispute(
            dispute=dispute,
            moderator=moderator,
            decision="hide",
            note="No engagement could be evidenced by either side.",
        )

    assert _sent_to("secretary@example.com")
    # The author finds out through the ordinary hide notification, with the
    # same reason on it - there is no version of events they are kept out of.
    [authors_copy] = _sent_to("buyer@example.com")
    assert "No engagement could be evidenced by either side." in authors_copy.body
