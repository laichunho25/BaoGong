"""Fixtures for the account tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.providers.models import ClaimStatus, Provider
from apps.providers.services import ensure_providers
from apps.registry.models import LicenceStatus, Licensee, allow_registry_writes

if TYPE_CHECKING:
    from collections.abc import Callable

#: Passes the character-mix rule in apps/core/password_validation.py. The
#: fixtures below create users through the manager, which does not validate,
#: but the view tests post this string at forms that do.
PASSWORD = "Correct-Horse9!"


@pytest.fixture
def make_user() -> Callable[..., User]:
    """Build a user. ``verified=True`` gives it a confirmed address.

    Verification is a separate argument rather than the default because most
    of what is tested here is what an account may *not* do before it is
    verified - including sign in at all.
    """
    counter = {"n": 0}

    def _make(*, verified: bool = False, **overrides: Any) -> User:
        counter["n"] += 1
        fields: dict[str, Any] = {
            "email": f"user{counter['n']}@example.com",
            "password": PASSWORD,
            "role": Role.BUYER,
        }
        fields.update(overrides)
        if verified:
            fields["email_verified_at"] = timezone.now()
        return User.objects.create_user(**fields)

    return _make


@pytest.fixture
def make_provider() -> Callable[..., Provider]:
    """A claimed provider whose licence is on the register.

    The licensee is created inside ``allow_registry_writes`` because the
    register is read-only everywhere else (CLAUDE.md rule 1); a test that could
    write to it without opening the gate would prove the gate does not work.
    """
    counter = {"n": 0}

    def _make(**overrides: Any) -> Provider:
        counter["n"] += 1
        now = timezone.now()
        with allow_registry_writes():
            licensee = Licensee.objects.create(
                licence_no=f"TC{counter['n']:06d}",
                name_en=f"Test Company {counter['n']} Limited",
                business_address="1/F, Test Building, Central, Hong Kong",
                district="Central and Western",
                status=LicenceStatus.ACTIVE,
                first_seen_at=now,
                last_seen_at=now,
                last_synced_at=now,
                raw={},
            )
        ensure_providers(licence_nos=[licensee.licence_no])
        provider = Provider.objects.get(licensee=licensee)
        provider.claim_status = ClaimStatus.CLAIMED
        for field, value in overrides.items():
            setattr(provider, field, value)
        provider.save()
        return provider

    return _make
