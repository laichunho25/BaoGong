"""Fixtures for the provider layer.

Licensees are created through ``allow_registry_writes`` because the register is
read-only everywhere else (CLAUDE.md rule 1); a test that could write to it
without opening the gate would prove the gate does not work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import Role, User
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


#: A minimal but genuine PDF: ``inspect_upload`` sniffs the leading bytes, so a
#: file of zeros would be rejected for the right reason and prove nothing.
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


@pytest.fixture
def make_user() -> Callable[..., User]:
    counter = {"n": 0}

    def _make(*, verified: bool = True, **overrides: Any) -> User:
        counter["n"] += 1
        fields: dict[str, Any] = {
            "email": f"claimant{counter['n']}@example.com",
            "password": "correct-horse-battery",
            "role": Role.BUYER,
        }
        fields.update(overrides)
        user = User.objects.create_user(**fields)
        if verified:
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified_at"])
        return user

    return _make


@pytest.fixture
def moderator(make_user: Callable[..., User]) -> User:
    return make_user(email="moderator@example.com", role=Role.MODERATOR)


@pytest.fixture
def make_upload() -> Callable[..., SimpleUploadedFile]:
    def _make(name: str = "br.pdf", content: bytes = PDF_BYTES) -> SimpleUploadedFile:
        return SimpleUploadedFile(name, content, content_type="application/pdf")

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
