"""Matching pages. Reads through selectors, writes through services.

The same request has two readers with opposite rights, so ``rfq_detail``
branches on who is asking rather than existing twice: the buyer sees the offers
and decides between them, a licensed company sees the requirement and may
answer it once. Nobody else sees anything - the wall is behind a login, because
a public wall of requirements is a lead list waiting to be scraped (COMPLIANCE
section 4).

Every page here renders the requirement and never the person who wrote it.
``Rfq.buyer`` exists in the template context only on the buyer's own pages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.permissions import is_provider_member, verified_email_required
from apps.agents import services as agent_services
from apps.providers.selectors import get_provider_detail
from apps.rfq import selectors, services
from apps.rfq.forms import (
    IntakeForm,
    QuoteForm,
    QuoteLineItemFormSet,
    RfqForm,
    initial_from_intake,
    line_items_from,
)
from apps.rfq.models import LineItemLabel, Quote, RfqStatus

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.rfq.models import Rfq

#: How many requests one page of the wall shows. No pagination yet: the wall is
#: young enough that a longer list is a better answer than a second page.
WALL_LIMIT = 100

#: Where A1's draft waits between the two requests. Server-side on purpose:
#: ``Rfq.structured`` is meant to be what the model actually said, so it must
#: not travel through a form the browser can edit.
INTAKE_SESSION_KEY = "rfq_intake_draft"


def _owned_rfq(request: HttpRequest, rfq_id: str) -> Rfq:
    rfq = selectors.get_rfq(rfq_id)
    if rfq is None or str(rfq.buyer_id) != str(request.user.pk):
        # 404 rather than 403: whether a request exists is not something an
        # unrelated account learns by guessing at ids.
        raise Http404("No such request")
    return rfq


def _provider_for_member(request: HttpRequest, slug: str) -> Provider:
    provider = get_provider_detail(slug)
    if provider is None or not is_provider_member(cast("User", request.user), provider):
        raise Http404("No such company")
    return provider


# --------------------------------------------------------------------- buyers


@login_required
@verified_email_required
def rfq_create(request: HttpRequest) -> HttpResponse:
    """Write a requirement. It lands as a draft and nobody sees it yet.

    Draft rather than straight to the wall because A1 pre-fills this form from
    the buyer's own words: what a model made of your business is something you
    get to read before licensed companies do (CLAUDE.md rule 3). The extra
    click is the price of that, and it is the same click either way.

    The agent's own draft rides along in the session, not in a hidden field.
    ``Rfq.structured`` is the record of what the model said, and a copy of it
    the browser could edit on the way back would be a record of nothing.
    """
    buyer = cast("User", request.user)
    if request.method == "POST":
        form = RfqForm(request.POST)
        if form.is_valid():
            draft = request.session.get(INTAKE_SESSION_KEY) or {}
            try:
                rfq = services.create_rfq(
                    buyer=buyer,
                    structured=draft.get("structured", {}),
                    is_ai_assisted=bool(draft),
                    **form.to_service_kwargs(),
                )
            except services.RfqError as exc:
                form.add_error(None, str(exc))
            else:
                request.session.pop(INTAKE_SESSION_KEY, None)
                messages.success(request, _("需求已保存为草稿，确认无误后再发布。"))
                return redirect("rfq:detail", rfq_id=str(rfq.pk))
    else:
        # A fresh visit is a fresh requirement; a draft left over from an
        # abandoned attempt must not attach itself to somebody's next one.
        request.session.pop(INTAKE_SESSION_KEY, None)
        form = RfqForm(initial={"currency": "HKD"})

    return render(
        request,
        "rfq/rfq_form.html",
        {"form": form, "intake_form": IntakeForm(), "intake": None},
    )


@login_required
@verified_email_required
@require_POST
def rfq_intake(request: HttpRequest) -> HttpResponse:
    """Read the buyer's paragraph with A1 and hand back a filled-in form.

    Writes no ``Rfq``. What comes back is the same form as ``rfq_create``, with
    boxes filled in and a notice above it saying who filled them in; the buyer
    corrects it and submits it themselves (AI_AGENTS A1). When the agent is off
    or the call fails, the keyword fallback fills in what it can and the notice
    says so - the page never becomes unusable because a model was unavailable.
    """
    intake_form = IntakeForm(request.POST)
    if not intake_form.is_valid():
        return render(
            request,
            "rfq/rfq_form.html",
            {"form": RfqForm(initial={"currency": "HKD"}), "intake_form": intake_form},
            status=422,
        )

    raw_input = intake_form.cleaned_data["raw_input"]
    result = agent_services.draft_rfq_prefill(raw_input=raw_input)
    structured = result.data.model_dump(mode="json")
    request.session[INTAKE_SESSION_KEY] = {"structured": structured, "run_id": result.run_id}

    return render(
        request,
        "rfq/rfq_form.html",
        {
            "form": RfqForm(initial=initial_from_intake(structured, raw_input)),
            "intake_form": IntakeForm(initial={"raw_input": raw_input}),
            "intake": {
                "questions": structured.get("clarifying_questions", []),
                "missing_fields": structured.get("missing_fields", []),
                "used_fallback": result.used_fallback,
                "confidence": result.confidence,
            },
        },
    )


@login_required
def my_rfqs(request: HttpRequest) -> HttpResponse:
    """The buyer's own requests, drafts included."""
    return render(
        request,
        "rfq/my_rfqs.html",
        {"rfqs": selectors.rfqs_for_buyer(cast("User", request.user))},
    )


@login_required
@require_POST
def rfq_publish(request: HttpRequest, rfq_id: str) -> HttpResponse:
    rfq = _owned_rfq(request, rfq_id)
    try:
        services.publish_rfq(rfq=rfq, buyer=cast("User", request.user))
    except services.RfqError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("需求已发布，持牌公司现在可以报价。"))
    return redirect("rfq:detail", rfq_id=str(rfq.pk))


@login_required
@require_POST
def rfq_close(request: HttpRequest, rfq_id: str) -> HttpResponse:
    rfq = _owned_rfq(request, rfq_id)
    try:
        services.close_rfq(
            rfq=rfq, buyer=cast("User", request.user), reason=request.POST.get("reason", "")
        )
    except services.RfqError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("需求已关闭，不再接收新报价。"))
    return redirect("rfq:detail", rfq_id=str(rfq.pk))


def _decide_quote(request: HttpRequest, quote_id: str, accept: bool) -> HttpResponse:
    quote = Quote.objects.select_related("rfq", "provider").filter(pk=quote_id).first()
    if quote is None or str(quote.rfq.buyer_id) != str(request.user.pk):
        raise Http404("No such quote")

    buyer = cast("User", request.user)
    action = services.accept_quote if accept else services.shortlist_quote
    try:
        action(quote=quote, buyer=buyer)
    except services.RfqError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            _("已选择该公司。我们会通知对方，其余报价将标记为未获选。")
            if accept
            # Said plainly because it is easy to assume otherwise: the platform
            # is an introduction, not a party to whatever is agreed next
            # (COMPLIANCE section 6).
            else _("已加入候选。"),
        )
    return redirect("rfq:detail", rfq_id=str(quote.rfq_id))


@login_required
@require_POST
def quote_shortlist(request: HttpRequest, quote_id: str) -> HttpResponse:
    return _decide_quote(request, quote_id, accept=False)


@login_required
@require_POST
def quote_accept(request: HttpRequest, quote_id: str) -> HttpResponse:
    return _decide_quote(request, quote_id, accept=True)


# ------------------------------------------------------------------ companies


@login_required
def rfq_wall(request: HttpRequest) -> HttpResponse:
    """Open requests, and what it would cost this company to answer them.

    The quota is shown before anything is spent rather than after: "you have
    one left today" is a different decision from "that was your last one".
    """
    user = cast("User", request.user)
    providers = selectors.quotable_providers(user)
    return render(
        request,
        "rfq/wall.html",
        {
            "rfqs": selectors.open_rfqs()[:WALL_LIMIT],
            "quotas": [(provider, selectors.quota_state(provider)) for provider in providers],
            "can_quote": bool(providers),
        },
    )


@login_required
@verified_email_required
def quote_create(request: HttpRequest, rfq_id: str, slug: str) -> HttpResponse:
    """Answer one request in one company's name.

    The company is in the URL rather than inferred from the account, because a
    person can belong to more than one and an offer sent under the wrong name
    is not a UX slip - it is a licensed company quoting for work it never saw.
    """
    provider = _provider_for_member(request, slug)
    rfq = selectors.get_rfq(rfq_id)
    if rfq is None or not rfq.is_open:
        raise Http404("No such request")

    member = cast("User", request.user)
    if rfq.is_full:
        # Turned away at the door rather than after a price breakdown has been
        # typed out. The service refuses this too - this is only the courtesy.
        messages.info(request, _("该需求的报价名额已满，看看需求墙上其他需求。"))
        return redirect("rfq:wall")

    if selectors.has_quoted(rfq=rfq, provider=provider):
        messages.info(request, _("贵公司已经就该需求报过价。"))
        return redirect("rfq:detail", rfq_id=str(rfq.pk))

    if request.method == "POST":
        form = QuoteForm(request.POST)
        formset = QuoteLineItemFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            payload = form.to_service_kwargs()
            try:
                services.submit_quote(
                    rfq=rfq,
                    provider=provider,
                    submitted_by=member,
                    line_items=line_items_from(formset, payload["currency"]),
                    **payload,
                )
            except services.QuotaExceeded as exc:
                # Its own branch because this is the one refusal that ends in an
                # offer to buy something rather than in a correction.
                messages.warning(request, str(exc))
                return redirect("rfq:wall")
            except services.RfqError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, _("报价已发送，客户会收到通知。"))
                return redirect("rfq:detail", rfq_id=str(rfq.pk))
    else:
        form = QuoteForm(initial={"currency": rfq.currency, "validity_days": 14})
        formset = QuoteLineItemFormSet()

    return render(
        request,
        "rfq/quote_form.html",
        {
            "rfq": rfq,
            "provider": provider,
            "form": form,
            "formset": formset,
            "quota": selectors.quota_state(provider),
        },
    )


@login_required
@require_POST
def quote_withdraw(request: HttpRequest, quote_id: str) -> HttpResponse:
    quote = Quote.objects.select_related("rfq", "provider").filter(pk=quote_id).first()
    if quote is None or not is_provider_member(cast("User", request.user), quote.provider):
        raise Http404("No such quote")

    try:
        services.withdraw_quote(quote=quote, member=cast("User", request.user))
    except services.RfqError as exc:
        messages.error(request, str(exc))
    else:
        messages.info(request, _("报价已撤回。已用掉的报价额度不会退回。"))
    return redirect("rfq:detail", rfq_id=str(quote.rfq_id))


# ---------------------------------------------------------------- both sides


@login_required
def rfq_detail(request: HttpRequest, rfq_id: str) -> HttpResponse:
    """One request, seen from whichever side is asking."""
    rfq = selectors.get_rfq(rfq_id)
    if rfq is None:
        raise Http404("No such request")

    user = cast("User", request.user)
    if str(rfq.buyer_id) == str(user.pk):
        quotes = list(selectors.quotes_for_rfq(rfq))
        labels = dict(LineItemLabel.choices)
        for quote in quotes:
            # Named, not counted: "没有报政府费用" is actionable and "缺 3 项" is
            # not. This is A5's rule-based half and it works with every model
            # switched off (AI_AGENTS A5).
            quote.missing_labels = [  # type: ignore[attr-defined]
                str(labels[label]) for label in selectors.missing_standard_items(quote)
            ]
        return render(
            request,
            "rfq/detail_buyer.html",
            {
                "rfq": rfq,
                "quotes": quotes,
                # The whole point of the standard label list: two prices on one
                # basis, with the gaps left visible.
                "comparison_rows": selectors.comparison_rows(quotes),
                # A2's reading list. Suggestions only, and only to the buyer -
                # no company is told it was suggested or passed over.
                "matches": selectors.suggested_matches(rfq),
                "matches_used_fallback": bool((rfq.matches or {}).get("used_fallback")),
            },
        )

    # Anyone else may only see a request that is actually on the wall, and a
    # draft or a finished one is nobody else's business.
    if rfq.status != RfqStatus.OPEN or rfq.has_expired:
        raise Http404("No such request")

    providers = selectors.quotable_providers(user)
    return render(
        request,
        "rfq/detail_provider.html",
        {
            "rfq": rfq,
            "entries": [
                {
                    "provider": provider,
                    "quota": selectors.quota_state(provider),
                    "quote": Quote.objects.filter(rfq=rfq, provider=provider)
                    .exclude(status="withdrawn")
                    .first(),
                }
                for provider in providers
            ],
        },
    )
