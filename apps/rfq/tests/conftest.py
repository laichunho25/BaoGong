"""Fixtures for the matching layer.

The account and company factories come from the provider layer rather than
being rewritten here: a second ``make_provider`` that drifted from the first
would let quote tests pass against a company shape that no longer exists.

``make_quoting_provider`` is the one addition, because "a company that may
quote" is a real state with three parts - claimed, on the register, and with a
member signed in - and every test that forgets one of them would be testing the
refusal instead of the feature.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from apps.accounts.models import ProviderMember, Role
from apps.providers.models import ClaimStatus
from apps.providers.tests.conftest import (  # noqa: F401  (re-exported fixtures)
    make_licensee,
    make_provider,
    make_upload,
    make_user,
    moderator,
)
from apps.reviews.tests.conftest import make_review  # noqa: F401  (re-exported fixture)
from apps.rfq import services

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.rfq.models import Rfq


@pytest.fixture
def buyer(make_user: Callable[..., User]) -> User:  # noqa: F811
    return make_user(email="buyer@example.com")


@pytest.fixture
def make_quoting_provider(
    make_provider: Callable[..., Provider],  # noqa: F811
    make_user: Callable[..., User],  # noqa: F811
) -> Callable[..., tuple[Provider, User]]:
    """A claimed, licensed company plus a member who may act for it."""
    counter = {"n": 0}

    def _make(**overrides: Any) -> tuple[Provider, User]:
        counter["n"] += 1
        provider = make_provider(claim_status=ClaimStatus.CLAIMED, **overrides)
        member = make_user(email=f"secretary{counter['n']}@example.com", role=Role.PROVIDER_MEMBER)
        ProviderMember.objects.create(user=member, provider=provider)
        return provider, member

    return _make


@pytest.fixture
def open_rfq(buyer: User) -> Rfq:
    rfq = services.create_rfq(
        buyer=buyer,
        title="Incorporate a trading company and open a bank account",
        services_needed=["incorporation", "company_secretary", "bank_account_assist"],
        raw_input="I sell electronics to Europe and need a HK company plus an account.",
        needs_bank_account=True,
    )
    return services.publish_rfq(rfq=rfq, buyer=buyer)


@pytest.fixture
def quote_payload() -> dict[str, Any]:
    return {
        "first_year_total_minor": 1_200_00,
        "renewal_total_minor": 800_00,
        "includes_govt_fee": True,
        "delivery_days": 5,
        "line_items": [
            {"label": "govt_incorporation_fee", "amount_minor": 172_00},
            {"label": "incorporation_service", "amount_minor": 428_00},
            {"label": "company_secretary", "amount_minor": 600_00, "unit": "yearly"},
        ],
    }
