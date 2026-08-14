"""Fixtures for the provider layer.

Licensees are created through ``allow_registry_writes`` because the register is
read-only everywhere else (CLAUDE.md rule 1); a test that could write to it
without opening the gate would prove the gate does not work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.utils import timezone

from apps.providers.models import Provider
from apps.providers.services import ensure_providers
from apps.registry.models import LicenceStatus, Licensee, allow_registry_writes

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.fixture
def make_licensee() -> Callable[..., Licensee]:
    counter = {"n": 0}

    def _make(**overrides: Any) -> Licensee:
        counter["n"] += 1
        now = timezone.now()
        defaults: dict[str, Any] = {
            "licence_no": f"TC{counter['n']:06d}",
            "name_en": f"Test Company {counter['n']} Limited",
            "name_zh": "",
            "business_address": "1/F, Test Building, Central, Hong Kong",
            "district": "Central and Western",
            "status": LicenceStatus.ACTIVE,
            "first_seen_at": now,
            "last_seen_at": now,
            "last_synced_at": now,
            "raw": {},
        }
        defaults.update(overrides)
        with allow_registry_writes():
            return Licensee.objects.create(**defaults)

    return _make


@pytest.fixture
def make_provider(make_licensee: Callable[..., Licensee]) -> Callable[..., Provider]:
    def _make(licensee_kwargs: dict[str, Any] | None = None, **overrides: Any) -> Provider:
        licensee = make_licensee(**(licensee_kwargs or {}))
        ensure_providers(licence_nos=[licensee.licence_no])
        provider = Provider.objects.get(licensee=licensee)
        if overrides:
            for field, value in overrides.items():
                setattr(provider, field, value)
            provider.save()
        return provider

    return _make
