"""The development fixture.

Two things are worth a test here, and they are not "does it write rows". One
is the guard: this command puts invented reviews and prices under the names of
real licensed companies, so running it anywhere but a debug box is the failure
that matters. The other is that it goes through the services - a fixture that
drifts into writing columns directly stops demonstrating the product and
starts demonstrating itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import get_resolver

from apps.accounts.models import ProviderMember, User
from apps.core.management.commands.seed_demo import SEED_DOMAIN
from apps.providers.models import ClaimStatus, PriceItem, Provider, ServiceOffering
from apps.reviews.models import Review, ReviewStatus
from apps.rfq.models import Quote, Rfq, RfqStatus

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.django_db


@pytest.fixture
def register(make_provider: Callable[..., Provider]) -> list[Provider]:
    return [make_provider() for _ in range(6)]


def _seed(**kwargs: object) -> None:
    call_command("seed_demo", providers=4, claimed=2, **kwargs)


@pytest.fixture
def debug_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the guard's switch without turning DEBUG on for Django itself.

    A URLconf loaded while DEBUG is on tries to mount django-debug-toolbar, an
    app the test settings deliberately do not install. So the routes are
    resolved first and the flag flipped after, on the settings object directly
    rather than through the override that would reload them again.
    """
    get_resolver().url_patterns  # noqa: B018 - force the import while DEBUG is off.
    monkeypatch.setattr(django_settings, "DEBUG", True)


def test_it_refuses_to_run_without_debug(register: list[Provider]) -> None:
    """The guard is the point: production holds the names of real companies."""

    with pytest.raises(CommandError, match="DEBUG"):
        _seed()

    assert not ServiceOffering.objects.exists()


def test_it_refuses_an_empty_register(debug_on: None) -> None:

    with pytest.raises(CommandError, match="sync_tcsp"):
        _seed()


def test_it_fills_the_pages_the_directory_renders(debug_on: None, register: list[Provider]) -> None:

    _seed()

    assert ServiceOffering.objects.count() > 0
    assert PriceItem.objects.count() > 0
    # A quarter of the pool publishes nothing, because the real directory does.
    assert ServiceOffering.objects.filter(prices__isnull=True).exists()
    assert Provider.objects.filter(claim_status=ClaimStatus.CLAIMED).count() == 2
    assert ProviderMember.objects.count() == 2


def test_reviews_land_in_every_state_the_tab_can_show(
    debug_on: None, register: list[Provider]
) -> None:

    _seed()

    assert Review.objects.filter(status=ReviewStatus.PUBLISHED, is_verified=True).exists()
    assert Review.objects.filter(status=ReviewStatus.PUBLISHED, is_verified=False).exists()
    # One is deliberately left undecided: the moderation queue is part of the
    # thing being demonstrated.
    assert Review.objects.filter(status=ReviewStatus.PENDING_MODERATION).exists()
    assert Provider.objects.exclude(rating_cached=None).exists()


def test_the_open_requirement_has_answers_and_a_shortlist(
    debug_on: None, register: list[Provider]
) -> None:

    _seed()

    rfq = Rfq.objects.get(status=RfqStatus.OPEN)
    assert rfq.matches.get("items")
    assert Quote.objects.filter(rfq=rfq).count() >= 1
    assert Quote.objects.exclude(analysis={}).exists()
    # Draft as well as open: the buyer's own list has two states in it.
    assert Rfq.objects.filter(status=RfqStatus.DRAFT).exists()


def test_running_it_twice_changes_nothing(debug_on: None, register: list[Provider]) -> None:
    """A fixture that doubles its own rows cannot be re-run after a code change."""

    _seed()
    counts = (Review.objects.count(), Rfq.objects.count(), Quote.objects.count())

    _seed()

    assert (Review.objects.count(), Rfq.objects.count(), Quote.objects.count()) == counts


def test_reset_takes_back_what_it_wrote(debug_on: None, register: list[Provider]) -> None:
    _seed()

    call_command("seed_demo", reset=True)

    assert not User.objects.filter(email__endswith=f"@{SEED_DOMAIN}").exists()
    assert not Review.objects.exists()
    assert not Rfq.objects.exists()
    assert not Quote.objects.exists()
    assert not ServiceOffering.objects.exists()
    assert not Provider.objects.exclude(claim_status=ClaimStatus.UNCLAIMED).exists()
    # The register itself is untouched - it was never this command's to write.
    assert Provider.objects.count() == len(register)


def test_a_page_somebody_else_claimed_is_stepped_over(
    debug_on: None,
    register: list[Provider],
) -> None:
    """Their claim stays theirs, and ``--reset`` never has to touch it."""
    theirs = register[0]
    theirs.claim_status = ClaimStatus.CLAIMED
    theirs.save(update_fields=["claim_status"])

    _seed()

    theirs.refresh_from_db()
    assert not ProviderMember.objects.filter(provider=theirs).exists()
    assert theirs.claim_status == ClaimStatus.CLAIMED
    assert Provider.objects.filter(claim_status=ClaimStatus.CLAIMED).count() == 3
