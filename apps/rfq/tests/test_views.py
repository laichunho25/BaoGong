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


# ------------------------------------------------------------------- the prefill


def test_reading_a_buyers_paragraph_creates_nothing(
    client: Client, buyer: User, settings: Any
) -> None:
    """A1's red line, at the layer a buyer can actually reach: pressing "fill
    the form in for me" puts nothing on the wall and nothing in the database
    (AI_AGENTS A1, CLAUDE.md rule 3)."""
    settings.AGENTS_ENABLED = False
    client.force_login(buyer)

    response = client.post(
        reverse("rfq:intake"),
        {"raw_input": "我想在香港注册公司做电商，需要注册地址和公司秘书，还想开个银行账户。"},
    )

    assert response.status_code == 200
    assert Rfq.objects.count() == 0


def test_the_prefill_fills_the_form_and_says_who_filled_it(
    client: Client, buyer: User, settings: Any
) -> None:
    settings.AGENTS_ENABLED = False
    client.force_login(buyer)

    response = client.post(
        reverse("rfq:intake"),
        {"raw_input": "想注册一家香港公司，需要注册地址和公司秘书，另外想开一个银行账户。"},
    )

    form = response.context["form"]
    assert "incorporation" in form.initial["services_needed"]
    assert response.context["intake"]["used_fallback"] is True
    # The buyer is told a machine did this, not left to assume they typed it.
    assert "关键词" in response.content.decode()


def test_the_confirmed_form_is_what_gets_saved_not_the_prefill(
    client: Client, buyer: User, settings: Any
) -> None:
    """The prefill is a suggestion. What reaches the wall is whatever the buyer
    left in the boxes after correcting it."""
    settings.AGENTS_ENABLED = False
    client.force_login(buyer)
    client.post(
        reverse("rfq:intake"),
        {"raw_input": "我在深圳做外贸，想注册一家香港公司，另外每年的做账报税也要一起做。"},
    )

    client.post(reverse("rfq:create"), RFQ_FORM)

    rfq = Rfq.objects.get()
    assert rfq.services_needed == ["incorporation", "company_secretary"]
    assert rfq.is_ai_assisted is True
    # What the model said is kept beside the buyer's answer, not merged into it.
    assert "accounting" in rfq.structured["services_needed"]


def test_a_form_submitted_without_the_prefill_is_not_marked_as_assisted(
    client: Client, buyer: User
) -> None:
    client.force_login(buyer)

    client.post(reverse("rfq:create"), RFQ_FORM)

    rfq = Rfq.objects.get()
    assert rfq.is_ai_assisted is False
    assert rfq.structured == {}


def test_an_abandoned_prefill_does_not_attach_to_the_next_requirement(
    client: Client, buyer: User, settings: Any
) -> None:
    settings.AGENTS_ENABLED = False
    client.force_login(buyer)
    client.post(
        reverse("rfq:intake"),
        {"raw_input": "我想注册一家香港公司做电商，后面每年的做账和报税也要一起做。"},
    )

    # The buyer walks away, comes back, and starts again from a blank form.
    client.get(reverse("rfq:create"))
    client.post(reverse("rfq:create"), RFQ_FORM)

    rfq = Rfq.objects.get()
    assert rfq.is_ai_assisted is False


def test_a_paragraph_too_short_to_read_is_refused(client: Client, buyer: User) -> None:
    client.force_login(buyer)

    response = client.post(reverse("rfq:intake"), {"raw_input": "开公司"})

    assert response.status_code == 422
    assert Rfq.objects.count() == 0


def test_both_description_boxes_print_the_words_a_quote_depends_on(
    client: Client, buyer: User
) -> None:
    """A buyer who leaves out what they sell and where gets a price nobody can
    stand behind, so the topics are printed under the box - on the one-box
    intake and on the full form alike, which is why they live on the field."""
    client.force_login(buyer)

    html = client.get(reverse("rfq:create")).content.decode()

    assert html.count("经营范围") == 2
    assert html.count("交易地区") == 2
    # The hints are prompts, never prefilled text: the requirement has to stay
    # the buyer's own words for a company to be able to price it.
    assert "经营范围</textarea>" not in html


def test_an_anonymous_visitor_cannot_use_the_prefill(client: Client) -> None:
    """It costs money per call, and it is not a service the platform offers to
    the open internet."""
    response = client.post(reverse("rfq:intake"), {"raw_input": "想注册香港公司做电商。"})

    assert response.status_code == 302
    assert "/accounts/login" in response.url


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


def test_the_wall_marks_a_verified_buyer_without_naming_them(
    client: Client,
    open_rfq: Rfq,
    make_provider: Callable[..., Provider],
    make_review: Callable[..., object],
    make_quoting_provider: Callable[..., tuple[Provider, User]],
) -> None:
    """What a company actually gets out of the home page's invitation: a badge
    saying a document behind this buyer has been checked. Still no name, no
    email, no id - the mark is an attribute of the requirement (COMPLIANCE
    section 4)."""
    _, member = make_quoting_provider()
    client.force_login(member)

    before = client.get(reverse("rfq:wall")).content.decode()
    assert "已核实用家" not in before

    make_review(provider=make_provider(), author=open_rfq.buyer, is_verified=True)

    after = client.get(reverse("rfq:wall")).content.decode()
    assert "已核实用家" in after
    assert open_rfq.buyer.email not in after
    assert str(open_rfq.buyer_id) not in after


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


# ------------------------------------------------------------- A2 suggestions


def test_the_shortlist_is_shown_to_the_buyer_with_what_made_it(
    client: Client,
    open_rfq: Rfq,
    buyer: User,
    make_provider: Callable[..., Provider],
) -> None:
    """AI_AGENTS A2. Suggestions carry their reasons and say out loud that a
    machine wrote them - a bare list of names reads as an endorsement."""
    provider = make_provider()
    open_rfq.matches = {
        "items": [{"provider_id": provider.slug, "rank": 1, "reasons": ["提供简体中文服务"]}],
        "used_fallback": True,
    }
    open_rfq.save(update_fields=["matches"])
    client.force_login(buyer)

    page = client.get(reverse("rfq:detail", args=[open_rfq.pk])).content.decode()

    assert provider.display_name in page
    assert "提供简体中文服务" in page
    assert "未经人工审阅" in page


def test_a_company_is_never_told_it_was_suggested(
    client: Client,
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    make_provider: Callable[..., Provider],
) -> None:
    """The shortlist is the buyer's reading list. Showing it on the company
    side would turn a suggestion into a standing, and a rejection into news."""
    suggested = make_provider()
    open_rfq.matches = {
        "items": [{"provider_id": suggested.slug, "rank": 1}],
        "used_fallback": True,
    }
    open_rfq.save(update_fields=["matches"])
    _, member = make_quoting_provider()
    client.force_login(member)

    page = client.get(reverse("rfq:detail", args=[open_rfq.pk])).content.decode()

    assert suggested.display_name not in page
