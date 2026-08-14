"""Fixtures for the account tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from apps.accounts.models import Role, User

if TYPE_CHECKING:
    from collections.abc import Callable

PASSWORD = "correct-horse-battery"


@pytest.fixture
def make_user() -> Callable[..., User]:
    counter = {"n": 0}

    def _make(**overrides: Any) -> User:
        counter["n"] += 1
        fields: dict[str, Any] = {
            "email": f"user{counter['n']}@example.com",
            "password": PASSWORD,
            "role": Role.BUYER,
        }
        fields.update(overrides)
        return User.objects.create_user(**fields)

    return _make
