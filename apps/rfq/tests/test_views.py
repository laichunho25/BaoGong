"""The pages a buyer and a company actually touch.

Two of these tests are the product promises made checkable: a request wall that
never renders who wrote the request (COMPLIANCE section 4), and a comparison
table that shows the gap where a company did not price a standard item. The
rest are the refusals - the wall is behind a login, an unclaimed company gets no
quote entry, and one request is nobody else's business.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.urls import reverse

from apps.providers.models import ClaimStatus
from apps.rfq import services
from apps.rfq.models import Quote, QuoteStatus, Rfq, RfqStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from apps.accounts.models import User
    from apps.providers.models import Provider

pytestmark = pytest.mark.django_db

RFQ_FORM: dict[str, Any] = {
    "title": "Incorporate a trading company",
    "raw_input": "I sell electronics into Europe and need a Hong Kong company plus an account.",
    "company_type": "hk_private_limited",
    "business_nature": "Cross-border e-commerce",
    "shareholder_nationalities": "CN, HK",
    "services_needed": ["incorporation", "company_secretary"],
    "needs_bank_account": "on",
    "timeline": "asap",
    "currency": "HKD",
    "budget_min": "5000",
    "budget_max": "12000",
}


def quote_form(**overrides: Any) -> dict[str, Any]:
    """A filled quote form plus its line-item formset, in POST shape."""
    data: dict[str, Any] = {
        "currency": "HKD",
        "first_year_total": "6800.00",
        "renewal_total": "3200.00",
        "includes_govt_fee": "on",
        "delivery_days": "5",
        "validity_days": "14",
        "message": "",
        "form-TOTAL_FORMS": "6",
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
    }
    for index in range(6):
        data[f"form-{index}-label"] = ""
        data[f"form-{index}-amount"] = ""
        data[f"form-{index}-unit"] = "one_off"
        data[f"form-{index}-custom_label"] = ""
        data[f"form-{index}-note"] = ""
    data["form-0-label"] = "govt_incorporation_fee"
    data["form-0-amount"] = "1720.00"
    data["form-1-label"] = "company_secretary"
    data["form-1-amount"] = "1800.00"
    data["form-1-unit"] = "yearly"
    data.update(overrides)
    return data


# ---------------------------------------------------------------------- buyers


def test_an_anonymous_visitor_cannot_read_the_request_wall(client: Client) -> None:
    """The wall is requirements written by people who were promised the wall
    would not become a lead list. Signing in is the least it can cost."""
    response = client.get(reverse("rfq:wall"))

    assert response.status_code == 302
    assert "/accounts/login" in response.url


def test_publishing_a_request_takes_two_steps(client: Client, buyer: User) -> None:
    """Draft first, then published: what a model made of your business is
    something you get to read before licensed companies do."""
    client.force_login(buyer)

    response = client.post(reverse("rfq:create"), RFQ_FORM)

    rfq = Rfq.objects.get()
    assert rfq.status == RfqStatus.DRAFT
    assert rfq.buyer_id == buyer.pk
    assert rfq.shareholder_nationalities == ["CN", "HK"]
    # Typed in major units, stored in minor ones (CLAUDE.md rule 6).
    assert (rfq.budget_min_minor, rfq.budget_max_minor) == (500_000, 1_200_000)
    assert response.status_code == 302

    client.post(reverse("rfq:publish", args=[rfq.pk]))

    rfq.refresh_from_db()
    assert rfq.status == RfqStatus.OPEN
    assert rfq.expires_at is not None


def test_the_buyer_can_close_a_request_and_shortlist_before_deciding(
    client: Client,
    open_rfq: Rfq,
    buyer: User,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    """Shortlisting is not deciding: it keeps every other offer live, so a
    buyer can narrow the field without ending the request."""
    provider, member = make_quoting_provider()
    quote = services.submit_quote(
        rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload
    )
    client.force_login(buyer)

    client.post(reverse("rfq:quote_shortlist", args=[quote.pk]))
    quote.refresh_from_db()
    open_rfq.refresh_from_db()
    assert quote.status == QuoteStatus.SHORTLISTED
    assert open_rfq.status == RfqStatus.OPEN

    assert open_rfq.title in client.get(reverse("rfq:my_rfqs")).content.decode()

    client.post(reverse("rfq:close", args=[open_rfq.pk]), {"reason": "Went with an existing agent"})
    open_rfq.refresh_from_db()
    assert open_rfq.status == RfqStatus.CLOSED
    assert open_rfq.close_reason == "Went with an existing agent"


def test_one_buyer_cannot_open_another_buyers_request(
    client: Client, buyer: User, make_user: Callable[..., User]
) -> None:
    """A draft is not on the wall, so its existence is not public information -
    hence 404 rather than 403."""
    draft = services.create_rfq(
        buyer=buyer, title="Private draft", services_needed=["incorporation"]
    )
    client.force_login(make_user(email="someone-else@example.com"))

    response = client.get(reverse("rfq:detail", args=[draft.pk]))

    assert response.status_code == 404


# ------------------------------------------------------------------- companies


def test_the_wall_never_renders_the_buyer(
    client: Client,
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    """COMPLIANCE section 4. The wall carries a requirement, not a person."""
    _, member = make_quoting_provider()
    client.force_login(member)

    wall = client.get(reverse("rfq:wall")).content.decode()
    detail = client.get(reverse("rfq:detail", args=[open_rfq.pk])).content.decode()

    assert open_rfq.title in wall
    for page in (wall, detail):
        assert open_rfq.buyer.email not in page
        assert str(open_rfq.buyer_id) not in page


def test_an_unclaimed_company_is_offered_no_way_to_quote(
    client: Client,
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    """An offer in a licensed company's name comes from someone who proved they
    are that company. The page declines to show the button, and the service
    would refuse it anyway."""
    provider, member = make_quoting_provider()
    provider.claim_status = ClaimStatus.UNCLAIMED
    provider.save(update_fields=["claim_status"])
    client.force_login(member)

    page = client.get(reverse("rfq:detail", args=[open_rfq.pk])).content.decode()

    assert reverse("rfq:quote", args=[open_rfq.pk, provider.slug]) not in page
    assert client.post(reverse("rfq:quote", args=[open_rfq.pk, provider.slug]), quote_form())
    assert Quote.objects.count() == 0


def test_submitting_a_quote_spends_one_and_reaches_the_buyer(
    client: Client,
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Any,
) -> None:
    provider, member = make_quoting_provider()
    client.force_login(member)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("rfq:quote", args=[open_rfq.pk, provider.slug]), quote_form()
        )

    assert response.status_code == 302
    quote = Quote.objects.get()
    assert quote.first_year_total_minor == 680_000
    assert {item.label for item in quote.line_items.all()} == {
        "govt_incorporation_fee",
        "company_secretary",
    }
    # Blank formset rows are a person who did not fill something in, not data.
    assert quote.line_items.count() == 2
    assert len(mailoutbox) == 1
    assert open_rfq.buyer.email in mailoutbox[0].to
    # Notification rule 2: the conclusion and a link, never the numbers.
    assert "6,800" not in mailoutbox[0].body


def test_a_company_out_of_quota_is_sent_somewhere_it_can_do_something(
    client: Client,
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
    buyer: User,
) -> None:
    """The one refusal on the platform that ends in an offer to buy something,
    so it must not look like a validation error on the form."""
    provider, member = make_quoting_provider()
    for index in range(3):
        other = services.create_rfq(
            buyer=buyer, title=f"Other request {index}", services_needed=["incorporation"]
        )
        services.submit_quote(
            rfq=services.publish_rfq(rfq=other, buyer=buyer),
            provider=provider,
            submitted_by=member,
            **quote_payload,
        )
    client.force_login(member)

    response = client.post(reverse("rfq:quote", args=[open_rfq.pk, provider.slug]), quote_form())

    assert response.status_code == 302
    assert response.url == reverse("rfq:wall")
    assert Quote.objects.filter(rfq=open_rfq).count() == 0


# ------------------------------------------------------------------- the table


def test_the_comparison_table_shows_the_gap_a_cheap_quote_hides(
    client: Client,
    open_rfq: Rfq,
    buyer: User,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    """A blank cell is the product. One company priced the government fee and
    the other did not, and the buyer is told which is which by name."""
    cheap_provider, cheap_member = make_quoting_provider()
    full_provider, full_member = make_quoting_provider()
    services.submit_quote(
        rfq=open_rfq,
        provider=cheap_provider,
        submitted_by=cheap_member,
        first_year_total_minor=400_000,
        line_items=[{"label": "incorporation_service", "amount_minor": 400_000}],
    )
    services.submit_quote(
        rfq=open_rfq,
        provider=full_provider,
        submitted_by=full_member,
        first_year_total_minor=680_000,
        line_items=[
            {"label": "incorporation_service", "amount_minor": 508_000},
            {"label": "govt_incorporation_fee", "amount_minor": 172_000},
        ],
    )
    client.force_login(buyer)

    response = client.get(reverse("rfq:detail", args=[open_rfq.pk]))
    page = response.content.decode()

    rows = {row.label: row for row in response.context["comparison_rows"]}
    assert set(rows) == {"incorporation_service", "govt_incorporation_fee"}
    # Cheapest first, so the company that left the fee out is the first column.
    assert rows["govt_incorporation_fee"].cells[0].amount_minor is None
    assert rows["govt_incorporation_fee"].cells[1].amount_minor == 172_000
    # And the gap is named on the quote itself, not merely counted.
    assert "Government incorporation fee" in page or "政府" in page


def test_choosing_a_company_tells_the_ones_that_were_not_chosen(
    client: Client,
    open_rfq: Rfq,
    buyer: User,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Any,
) -> None:
    """Every company here spent one of three daily quotes to be in this list.
    A marketplace that only mails the winner leaves the rest paying for
    silence."""
    winner, winner_member = make_quoting_provider()
    loser, loser_member = make_quoting_provider()
    chosen = services.submit_quote(
        rfq=open_rfq, provider=winner, submitted_by=winner_member, **quote_payload
    )
    services.submit_quote(rfq=open_rfq, provider=loser, submitted_by=loser_member, **quote_payload)
    client.force_login(buyer)
    mailoutbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("rfq:quote_accept", args=[chosen.pk]))

    chosen.refresh_from_db()
    open_rfq.refresh_from_db()
    assert chosen.status == QuoteStatus.ACCEPTED
    assert open_rfq.status == RfqStatus.AWARDED
    assert Quote.objects.get(provider=loser).status == QuoteStatus.DECLINED

    recipients = {address for mail in mailoutbox for address in mail.to}
    assert winner_member.email in recipients
    assert loser_member.email in recipients
    # COMPLIANCE section 6: a choice was recorded, not an engagement.
    winner_mail = next(mail for mail in mailoutbox if winner_member.email in mail.to)
    assert "不代收费用" in winner_mail.body


def test_withdrawing_says_plainly_that_the_credit_is_gone(
    client: Client,
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    provider, member = make_quoting_provider()
    quote = services.submit_quote(
        rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload
    )
    client.force_login(member)

    response = client.post(reverse("rfq:quote_withdraw", args=[quote.pk]), follow=True)

    quote.refresh_from_db()
    assert quote.status == QuoteStatus.WITHDRAWN
    assert "不会退回" in response.content.decode()
