"""Writes to the matching layer: requests, quotes, and the daily quota.

Two rules here are the product, not implementation detail:

* **Only a company that proved it is the company may quote.** A quote is an
  offer made in a licensed company's name to a buyer who will act on it, so
  ``submit_quote`` requires an approved claim and an active membership. An
  unclaimed page cannot answer, however tempting it is for the size of the
  marketplace.
* **A free quote is spent when it is submitted, not when it is read, and it is
  never refunded.** Withdrawing a quote does not return the credit
  (``withdraw_quote``): if it did, "submit, withdraw, submit" would be an
  unlimited allowance, and the buyer would still have received every one of
  those mails. Three a day is the price of the free tier (PRD section 3.4).

The quota itself is a running-balance ledger rather than a counter, so the
question "why could my company not answer that request" has an answer with a
date on it - see ``_ledger_for``.

Two things are mailed from here, both for the same reason the moderation
decisions are: nobody sits refreshing a page waiting to be answered. A buyer is
told a quote arrived, and every company that spent one of its three daily
quotes on this request is told whether it was chosen - **including the ones
that were not**. A marketplace that only mails the winner leaves everyone else
paying to wait for silence.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.permissions import is_provider_member
from apps.accounts.selectors import provider_member_emails
from apps.agents import tasks as agent_tasks
from apps.core.money import Money, MoneyError
from apps.core.notifications import absolute_url, notify
from apps.providers.models import ClaimStatus
from apps.rfq.models import (
    QUOTABLE_STATUSES,
    LineItemLabel,
    QuotaLedger,
    Quote,
    QuoteLineItem,
    QuoteStatus,
    Rfq,
    RfqStatus,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from apps.accounts.models import User
    from apps.providers.models import Provider


class RfqError(Exception):
    """An action that must not proceed. The message is shown to the user."""


class QuotaExceeded(RfqError):
    """The company is out of quotes for today.

    Its own class because the caller does something different with it: this is
    the one refusal on the platform that ends in an offer to buy something, and
    a view that had to match on message text to know that would be a bug
    waiting for the first translation.
    """


# --------------------------------------------------------------------- requests


def _validate_budget(currency: str, minimum: int | None, maximum: int | None) -> None:
    try:
        for amount in (minimum, maximum):
            if amount is not None:
                Money(amount, currency)
    except MoneyError as exc:
        raise RfqError(str(exc)) from exc
    if minimum is not None and maximum is not None and maximum < minimum:
        raise RfqError(_("预算上限不能低于下限。"))


@transaction.atomic
def create_rfq(
    *,
    buyer: User,
    title: str,
    services_needed: Sequence[str],
    raw_input: str = "",
    structured: dict[str, Any] | None = None,
    is_ai_assisted: bool = False,
    **fields: Any,
) -> Rfq:
    """Create a request in ``draft``. Nothing is visible to companies yet.

    Draft is the default because A1 pre-fills this form: the buyer has to see
    what the model wrote about their business and confirm it before a licensed
    company reads it (CLAUDE.md rule 3).
    """
    if not title.strip():
        raise RfqError(_("请填写需求标题。"))
    if not services_needed:
        raise RfqError(_("请至少选择一项需要的服务。"))

    currency = str(fields.get("currency", "HKD"))
    _validate_budget(currency, fields.get("budget_min_minor"), fields.get("budget_max_minor"))

    return Rfq.objects.create(
        buyer=buyer,
        title=title.strip(),
        raw_input=raw_input.strip(),
        structured=structured or {},
        is_ai_assisted=is_ai_assisted,
        services_needed=list(services_needed),
        status=RfqStatus.DRAFT,
        **fields,
    )


@transaction.atomic
def publish_rfq(*, rfq: Rfq, buyer: User) -> Rfq:
    """Put a draft on the wall with an expiry on it."""
    if str(rfq.buyer_id) != str(buyer.pk):
        raise RfqError(_("只有需求发布者可以发布该需求。"))
    if not buyer.is_email_verified:
        # The buyer is about to be answered by real companies spending real
        # quota; an unverified mailbox means nobody can be answered at all.
        raise RfqError(_("请先验证邮箱，再发布需求。"))
    if rfq.status != RfqStatus.DRAFT:
        raise RfqError(_("该需求已经发布过了。"))
    if not rfq.services_needed:
        raise RfqError(_("请至少选择一项需要的服务。"))

    now = timezone.now()
    rfq.status = RfqStatus.OPEN
    rfq.published_at = now
    rfq.expires_at = now + timedelta(days=settings.RFQ_OPEN_DAYS)
    rfq.save(update_fields=["status", "published_at", "expires_at", "updated_at"])
    return rfq


@transaction.atomic
def close_rfq(*, rfq: Rfq, buyer: User, reason: str = "") -> Rfq:
    """The buyer stops taking quotes. Existing quotes stay readable to them."""
    if str(rfq.buyer_id) != str(buyer.pk):
        raise RfqError(_("只有需求发布者可以关闭该需求。"))
    if rfq.status not in (RfqStatus.OPEN, RfqStatus.DRAFT):
        raise RfqError(_("该需求已经结束。"))

    rfq.status = RfqStatus.CLOSED
    rfq.closed_at = timezone.now()
    rfq.close_reason = reason.strip()[:200]
    rfq.save(update_fields=["status", "closed_at", "close_reason", "updated_at"])
    return rfq


def expire_open_rfqs(*, now: Any = None) -> int:
    """Take requests off the wall once their deadline passes.

    Runs from a beat task. Deliberately a bulk update with no per-row hooks:
    expiry is not a decision about anybody, and a wall that only clears itself
    when someone opens the page is a wall that charges companies to answer
    requests that are months dead.
    """
    moment = now or timezone.now()
    return int(
        Rfq.objects.filter(status=RfqStatus.OPEN, expires_at__lte=moment).update(
            status=RfqStatus.EXPIRED, updated_at=moment
        )
    )


# ------------------------------------------------------------------- the quota


def _ledger_for(provider: Provider, day: date) -> QuotaLedger:
    """Today's row for ``provider``, locked, with the balance carried forward.

    Locked because two members of the same company clicking Submit at the same
    moment is the ordinary case, not the race-condition edge case: without
    ``select_for_update`` both would read ``free_used=2`` and both would send.
    """
    row = QuotaLedger.objects.select_for_update().filter(provider=provider, date=day).first()
    if row is not None:
        return row

    previous = QuotaLedger.objects.filter(provider=provider, date__lt=day).order_by("-date").first()
    carried = previous.paid_balance if previous else 0
    try:
        with transaction.atomic():
            return QuotaLedger.objects.create(provider=provider, date=day, paid_balance=carried)
    except IntegrityError:
        # Another transaction created today's row between the read and the
        # write. Re-read it under the lock rather than failing a submission.
        return QuotaLedger.objects.select_for_update().get(provider=provider, date=day)


def _spend_quote(provider: Provider, day: date) -> str:
    """Consume one quote from the free allowance, then from purchased credit."""
    ledger = _ledger_for(provider, day)
    if ledger.free_remaining > 0:
        ledger.free_used += 1
        ledger.save(update_fields=["free_used", "updated_at"])
        return "free"
    if ledger.paid_balance > 0:
        ledger.paid_used += 1
        ledger.paid_balance -= 1
        ledger.save(update_fields=["paid_used", "paid_balance", "updated_at"])
        return "paid"
    raise QuotaExceeded(
        _("今日免费报价额度已用完，购买额度后可继续报价。"),
    )


@transaction.atomic
def grant_quote_credits(
    *, provider: Provider, credits: int, day: date | None = None
) -> QuotaLedger:
    """Add purchased quotes to a company's balance.

    Lives here rather than in ``billing`` because the balance it moves is this
    app's; billing will call it once a payment settles, and until then it is
    also how support puts a wrongly-spent quote back.
    """
    if credits <= 0:
        raise RfqError(_("购买额度必须大于零。"))
    ledger = _ledger_for(provider, day or timezone.localdate())
    ledger.paid_balance += credits
    ledger.save(update_fields=["paid_balance", "updated_at"])
    return ledger


# ---------------------------------------------------------------------- quotes


def _check_may_quote(*, rfq: Rfq, provider: Provider, member: User) -> None:
    if not is_provider_member(member, provider):
        raise RfqError(_("只有该公司的成员可以代表公司报价。"))
    if provider.claim_status != ClaimStatus.CLAIMED:
        # An offer made in a licensed company's name has to come from someone
        # who proved they are that company (P3 claim flow).
        raise RfqError(_("请先完成公司认领，再向客户报价。"))
    if not provider.is_on_register:
        raise RfqError(_("公司不在官方持牌名单上，暂不能报价。"))
    if rfq.status not in QUOTABLE_STATUSES or rfq.has_expired:
        raise RfqError(_("该需求已经结束，不能再报价。"))


def _write_line_items(quote: Quote, line_items: Sequence[dict[str, Any]]) -> None:
    seen: set[str] = set()
    rows = []
    for ordinal, item in enumerate(line_items):
        label = str(item.get("label", ""))
        if label not in LineItemLabel.values:
            raise RfqError(_("报价明细含有无法比较的项目。"))
        if label != LineItemLabel.OTHER:
            if label in seen:
                raise RfqError(_("同一项目不能重复填写。"))
            seen.add(label)
        amount = item.get("amount_minor")
        if not isinstance(amount, int) or amount < 0:
            raise RfqError(_("报价金额必须是不小于零的整数（最小货币单位）。"))
        rows.append(
            QuoteLineItem(
                quote=quote,
                label=label,
                custom_label=str(item.get("custom_label", ""))[:120],
                amount_minor=amount,
                unit=item.get("unit", "one_off"),
                is_optional=bool(item.get("is_optional", False)),
                note=str(item.get("note", ""))[:200],
                ordinal=ordinal,
            )
        )
    QuoteLineItem.objects.bulk_create(rows)


@transaction.atomic
def submit_quote(
    *,
    rfq: Rfq,
    provider: Provider,
    submitted_by: User,
    first_year_total_minor: int,
    renewal_total_minor: int | None = None,
    currency: str = "HKD",
    line_items: Sequence[dict[str, Any]] = (),
    **fields: Any,
) -> Quote:
    """Answer a request, spending one of the company's quotes for the day.

    The quota is charged before the row is written and inside the same
    transaction, so a submission that fails any later check gives the credit
    back by rolling back - and a submission that succeeds cannot have been free.
    """
    _check_may_quote(rfq=rfq, provider=provider, member=submitted_by)

    try:
        Money(first_year_total_minor, currency)
        if renewal_total_minor is not None:
            Money(renewal_total_minor, currency)
    except MoneyError as exc:
        raise RfqError(str(exc)) from exc

    if (
        Quote.objects.filter(rfq=rfq, provider=provider)
        .exclude(status=QuoteStatus.WITHDRAWN)
        .exists()
    ):
        raise RfqError(_("贵公司已经就该需求报过价，请先撤回再重新报价。"))

    _spend_quote(provider, timezone.localdate())

    quote = Quote.objects.create(
        rfq=rfq,
        provider=provider,
        submitted_by=submitted_by,
        currency=currency,
        first_year_total_minor=first_year_total_minor,
        renewal_total_minor=renewal_total_minor,
        **fields,
    )
    _write_line_items(quote, line_items)

    # The company's name and a link, and no prices: the buyer reads the offer
    # signed in, where the comparison table can put it beside the others
    # (ARCHITECTURE section 3, notification rule 2).
    notify(
        template="rfq_new_quote",
        recipients=[rfq.buyer.email],
        context={
            "rfq_title": rfq.title,
            "provider_name": provider.display_name,
            "url": absolute_url(reverse("rfq:detail", args=[str(rfq.pk)])),
        },
    )

    # A5 reads the quote and files its questions beside it (P5-3). After commit,
    # so the worker cannot arrive before the line items it is meant to read;
    # dispatched here rather than from the view so every path that creates a
    # quote gets the same treatment. Nothing waits on it - the buyer's page
    # computes the missing-item list from the standard labels regardless.
    transaction.on_commit(lambda: agent_tasks.analyse_quote.delay(str(quote.pk)))
    return quote


@transaction.atomic
def withdraw_quote(*, quote: Quote, member: User) -> Quote:
    """Take an offer back. The quote spent to make it is not returned.

    Refunding it would make "submit, withdraw, submit" an unlimited allowance,
    and the buyer has already been shown the price either way.
    """
    if not is_provider_member(member, quote.provider):
        raise RfqError(_("只有该公司的成员可以撤回报价。"))
    if quote.status == QuoteStatus.ACCEPTED:
        raise RfqError(_("客户已选择该报价，不能撤回。"))
    if quote.status == QuoteStatus.WITHDRAWN:
        return quote

    quote.status = QuoteStatus.WITHDRAWN
    quote.withdrawn_at = timezone.now()
    quote.save(update_fields=["status", "withdrawn_at", "updated_at"])
    return quote


def _buyer_action(quote: Quote, buyer: User) -> None:
    if str(quote.rfq.buyer_id) != str(buyer.pk):
        raise RfqError(_("只有需求发布者可以处理报价。"))
    if not quote.is_live:
        raise RfqError(_("该报价已经失效。"))


@transaction.atomic
def shortlist_quote(*, quote: Quote, buyer: User) -> Quote:
    _buyer_action(quote, buyer)
    quote.status = QuoteStatus.SHORTLISTED
    quote.save(update_fields=["status", "updated_at"])
    return quote


@transaction.atomic
def accept_quote(*, quote: Quote, buyer: User) -> Quote:
    """Choose one company. The request closes and the others are told.

    The platform takes no payment here and holds no engagement: COMPLIANCE
    section 6 keeps it an introduction, not an intermediary. What this records
    is a buyer's choice, which is also the only honest input the ranking has
    for "do buyers actually pick this company".
    """
    _buyer_action(quote, buyer)
    now = timezone.now()

    quote.status = QuoteStatus.ACCEPTED
    quote.decided_at = now
    quote.save(update_fields=["status", "decided_at", "updated_at"])

    # Read before the update, because after it they are no longer "the ones
    # still waiting" and there would be nobody left to tell.
    losing = list(
        Quote.objects.filter(rfq=quote.rfq)
        .exclude(pk=quote.pk)
        .filter(status__in=(QuoteStatus.SUBMITTED, QuoteStatus.SHORTLISTED))
        .select_related("provider")
    )
    Quote.objects.filter(pk__in=[other.pk for other in losing]).update(
        status=QuoteStatus.DECLINED, decided_at=now, updated_at=now
    )

    rfq = quote.rfq
    rfq.status = RfqStatus.AWARDED
    rfq.closed_at = now
    rfq.save(update_fields=["status", "closed_at", "updated_at"])

    url = absolute_url(reverse("rfq:detail", args=[str(rfq.pk)]))
    for other in (quote, *losing):
        notify(
            template="quote_decided",
            recipients=provider_member_emails(other.provider),
            context={
                "chosen": other.pk == quote.pk,
                "rfq_title": rfq.title,
                "provider_name": other.provider.display_name,
                "url": url,
            },
        )
    return quote


def expire_stale_quotes(*, now: Any = None) -> int:
    """Retire offers whose own validity period has run out.

    A company writes "valid for 14 days" and means it; leaving the quote on the
    buyer's screen as a live option after that turns the company's own promise
    into the platform's misrepresentation. Accepted quotes are left alone - a
    deal already struck does not lapse because a date passed.
    """
    moment = now or timezone.now()
    deadline = models.ExpressionWrapper(
        models.F("submitted_at") + models.F("validity_days") * timedelta(days=1),
        output_field=models.DateTimeField(),
    )
    stale = (
        Quote.objects.filter(status__in=(QuoteStatus.SUBMITTED, QuoteStatus.SHORTLISTED))
        .annotate(expires_at=deadline)
        .filter(expires_at__lte=moment)
    )
    return int(
        Quote.objects.filter(pk__in=stale.values("pk")).update(
            status=QuoteStatus.EXPIRED, updated_at=moment
        )
    )
