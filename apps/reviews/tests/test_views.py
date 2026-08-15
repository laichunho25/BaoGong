"""The pages a buyer and a company actually touch.

The assertions are about what reaches the browser, because that is where the
review layer's promises are either kept or broken: an unpublished review must
not appear on a public page even though it exists, and an unverified one must
be labelled as not counting towards the score.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.urls import reverse

from apps.accounts.models import ProviderMember
from apps.reviews import services
from apps.reviews.models import Review, ReviewStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from apps.accounts.models import User
    from apps.providers.models import Provider

pytestmark = pytest.mark.django_db

FORM_DATA: dict[str, Any] = {
    "body": "They filed the incorporation in four days and explained every fee up front.",
    "price_transparency": "4.5",
    "responsiveness": "4.5",
    "bank_support": "",
    "professionalism": "4.5",
    "after_sales": "4.5",
}


def test_writing_a_review_needs_a_verified_email(
    client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
) -> None:
    """RATING_SYSTEM section 6. An unverified address is not an account anyone
    can be held to, and a review is a public statement about a named business."""
    provider = make_provider()
    client.force_login(make_user(verified=False))

    response = client.get(reverse("reviews:create", args=[provider.slug]))

    assert response.status_code == 302
    assert Review.objects.count() == 0


def test_an_anonymous_visitor_is_sent_to_sign_in(
    client: Client, make_provider: Callable[..., Provider]
) -> None:
    response = client.get(reverse("reviews:create", args=[make_provider().slug]))

    assert response.status_code == 302
    assert "/accounts/login" in response["Location"]


def test_submitting_the_form_creates_a_pending_review(
    client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
) -> None:
    provider = make_provider()
    client.force_login(make_user())

    response = client.post(reverse("reviews:create", args=[provider.slug]), FORM_DATA)

    assert response.status_code == 302
    review = Review.objects.get()
    assert review.status == ReviewStatus.PENDING_MODERATION
    # "did not use the bank service" survives as null, not as a zero score.
    assert review.score.bank_support is None


def test_a_second_review_is_redirected_rather_than_offered_a_form(
    client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    provider, author = make_provider(), make_user()
    make_review(provider=provider, author=author)
    client.force_login(author)

    response = client.get(reverse("reviews:create", args=[provider.slug]))

    assert response.status_code == 302
    assert response["Location"] == reverse("reviews:my_reviews")


def test_the_detail_page_shows_published_reviews_only(
    client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    provider = make_provider()
    make_review(provider=provider, author=make_user(), body="Published and readable.")
    make_review(
        provider=provider,
        author=make_user(),
        body="Still in the queue and nobody has read it.",
        status=ReviewStatus.PENDING_MODERATION,
    )

    body = client.get(provider.get_absolute_url()).content.decode()

    assert "Published and readable." in body
    assert "Still in the queue" not in body


def test_an_unverified_review_is_labelled_as_not_counting(
    client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    """Showing it without the label would let weight-0 text read as evidence."""
    provider = make_provider()
    make_review(provider=provider, author=make_user(), is_verified=False)

    body = client.get(provider.get_absolute_url()).content.decode()

    assert "不计入评分" in body


def test_a_company_with_no_reviews_shows_no_score(
    client: Client, make_provider: Callable[..., Provider]
) -> None:
    """RATING_SYSTEM section 4, at the only layer the visitor sees."""
    provider = make_provider()

    body = client.get(provider.get_absolute_url()).content.decode()

    assert "暂无已验证评价" in body
    # Not the prior's 5.00, and not a 0 either - no number at all.
    assert "5.00" not in body


def test_the_dimension_table_renders_the_score_and_its_gaps(
    client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    provider = make_provider()
    make_review(
        provider=provider,
        author=make_user(),
        overall="4.5",
        scores={"bank_support": None},
    )
    services.recompute_provider_rating(str(provider.pk))

    body = client.get(provider.get_absolute_url()).content.decode()

    assert "4.95" in body
    assert "开户协助" in body


def test_a_member_can_reply_from_the_detail_page(
    client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    provider = make_provider()
    review = make_review(provider=provider, author=make_user())
    member = make_user()
    ProviderMember.objects.create(user=member, provider=provider)
    client.force_login(member)

    response = client.post(
        reverse("reviews:reply", args=[review.pk]), {"body": "We refunded the filing fee."}
    )

    assert response.status_code == 302
    assert review.reply.body == "We refunded the filing fee."


def test_a_stranger_cannot_reply(
    client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    review = make_review(provider=make_provider(), author=make_user())
    client.force_login(make_user())

    client.post(reverse("reviews:reply", args=[review.pk]), {"body": "Not our client."})

    assert not hasattr(review, "reply")


def test_my_reviews_lists_the_ones_nobody_else_can_see(
    client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    author = make_user()
    make_review(
        provider=make_provider(),
        author=author,
        body="Waiting on a moderator.",
        status=ReviewStatus.PENDING_MODERATION,
    )
    client.force_login(author)

    body = client.get(reverse("reviews:my_reviews")).content.decode()

    assert "Waiting on a moderator." in body


def test_the_review_form_is_kept_out_of_search_results(
    client: Client,
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
) -> None:
    """A form page indexed under a company's name is a liability, not traffic."""
    client.force_login(make_user())

    body = client.get(reverse("reviews:create", args=[make_provider().slug])).content.decode()

    assert "noindex" in body
