"""The spam gate on the registration form."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import responses
from django.urls import reverse

from apps.accounts.models import User
from apps.core import turnstile

if TYPE_CHECKING:
    from django.test import Client


def test_it_is_off_when_no_key_is_configured(settings: pytest.FixtureRequest) -> None:
    # Local development has no Cloudflare account; the form still has to work.
    settings.TURNSTILE_SECRET = ""  # type: ignore[attr-defined]

    assert not turnstile.is_enabled()
    assert turnstile.verify("")


@responses.activate
def test_a_missing_response_fails_when_the_gate_is_on(settings: pytest.FixtureRequest) -> None:
    settings.TURNSTILE_SECRET = "secret"  # type: ignore[attr-defined]

    assert not turnstile.verify("")


@responses.activate
def test_cloudflare_decides(settings: pytest.FixtureRequest) -> None:
    settings.TURNSTILE_SECRET = "secret"  # type: ignore[attr-defined]
    responses.post(turnstile.VERIFY_URL, json={"success": False})

    assert not turnstile.verify("token")


@responses.activate
def test_an_outage_at_cloudflare_does_not_lock_people_out(
    settings: pytest.FixtureRequest,
) -> None:
    # The email verification loop is still in front of the new account; a
    # closed door here would block every registration during the outage.
    settings.TURNSTILE_SECRET = "secret"  # type: ignore[attr-defined]
    responses.post(turnstile.VERIFY_URL, status=502)

    assert turnstile.verify("token")


@pytest.mark.django_db
@responses.activate
def test_a_failed_challenge_stops_registration(
    client: Client, settings: pytest.FixtureRequest
) -> None:
    settings.TURNSTILE_SECRET = "secret"  # type: ignore[attr-defined]
    responses.post(turnstile.VERIFY_URL, json={"success": False})

    response = client.post(
        reverse("accounts:register"),
        {
            "email": "bot@example.com",
            "password": "correct-horse-battery",
            "role": "buyer",
            "cf-turnstile-response": "forged",
        },
    )

    assert response.status_code == 200
    assert not User.objects.exists()
