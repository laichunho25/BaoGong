"""The beat tasks behind the two deadlines: the wall's and each quote's."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from django.utils import timezone

from apps.rfq import services
from apps.rfq.models import Quote, QuoteStatus, Rfq, RfqStatus
from apps.rfq.tasks import expire_quotes, expire_rfqs

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider

pytestmark = pytest.mark.django_db


def test_the_sweep_only_touches_requests_past_their_deadline(open_rfq: Rfq) -> None:
    assert expire_rfqs() == 0

    Rfq.objects.filter(pk=open_rfq.pk).update(expires_at=timezone.now() - timedelta(hours=1))

    assert expire_rfqs() == 1
    open_rfq.refresh_from_db()
    assert open_rfq.status == RfqStatus.EXPIRED


def test_a_quote_lapses_on_the_date_its_own_company_set(
    open_rfq: Rfq,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    """The company wrote "valid for 7 days" and meant it; leaving the offer on
    the buyer's screen afterwards turns its promise into our misstatement."""
    provider, member = make_quoting_provider()
    quote = services.submit_quote(
        rfq=open_rfq,
        provider=provider,
        submitted_by=member,
        validity_days=7,
        **quote_payload,
    )

    assert expire_quotes() == 0

    Quote.objects.filter(pk=quote.pk).update(submitted_at=timezone.now() - timedelta(days=8))

    assert expire_quotes() == 1
    quote.refresh_from_db()
    assert quote.status == QuoteStatus.EXPIRED


def test_an_accepted_quote_does_not_lapse(
    open_rfq: Rfq,
    buyer: User,
    make_quoting_provider: Callable[..., tuple[Provider, User]],
    quote_payload: dict[str, Any],
) -> None:
    """A deal already struck does not come undone because a date passed."""
    provider, member = make_quoting_provider()
    quote = services.submit_quote(
        rfq=open_rfq, provider=provider, submitted_by=member, **quote_payload
    )
    services.accept_quote(quote=quote, buyer=buyer)
    Quote.objects.filter(pk=quote.pk).update(submitted_at=timezone.now() - timedelta(days=60))

    assert expire_quotes() == 0
    quote.refresh_from_db()
    assert quote.status == QuoteStatus.ACCEPTED
