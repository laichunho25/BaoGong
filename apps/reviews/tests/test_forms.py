"""The review form.

The form's job is to keep the nine legal score values legal and to put the
human challenge in front of the write. RATING_SYSTEM section 6 pairs a verified
account with a challenge; the account half is the view's, so the half tested
here is the one that stops a script filing a hundred reviews from one address.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import responses
from django.test import override_settings

from apps.core import turnstile
from apps.reviews.forms import SCORE_CHOICES, ReviewForm

DATA: dict[str, Any] = {
    "body": "They filed the incorporation in four days and explained every fee up front.",
    "price_transparency": "4.5",
    "responsiveness": "4.5",
    "bank_support": "",
    "professionalism": "4.5",
    "after_sales": "4.5",
}


def test_the_score_options_are_the_nine_documented_values() -> None:
    """1 to 5 in halves, highest first (RATING_SYSTEM section 3). A number
    input would invite the tenth value and the DB would reject it later."""
    assert [value for value, _label in SCORE_CHOICES] == [
        "5.0",
        "4.5",
        "4.0",
        "3.5",
        "3.0",
        "2.5",
        "2.0",
        "1.5",
        "1.0",
    ]


def test_a_blank_bank_support_becomes_none_not_zero() -> None:
    form = ReviewForm(DATA)

    assert form.is_valid(), form.errors
    assert form.scores()["bank_support"] is None
    assert form.scores()["responsiveness"] == Decimal("4.5")


def test_an_off_scale_score_is_refused() -> None:
    form = ReviewForm({**DATA, "responsiveness": "4.7"})

    assert not form.is_valid()
    assert "responsiveness" in form.errors


def test_a_one_line_review_is_refused() -> None:
    """A public statement about a named business needs enough detail that the
    company can answer it."""
    form = ReviewForm({**DATA, "body": "bad"})

    assert not form.is_valid()
    assert "body" in form.errors


@responses.activate
@override_settings(TURNSTILE_SECRET="test-secret")
def test_a_failed_challenge_stops_the_review() -> None:
    responses.add(responses.POST, turnstile.VERIFY_URL, json={"success": False})

    form = ReviewForm({**DATA, turnstile.FIELD_NAME: "token"}, remote_ip="203.0.113.9")

    assert not form.is_valid()


@responses.activate
@override_settings(TURNSTILE_SECRET="test-secret")
def test_a_passed_challenge_lets_the_review_through() -> None:
    responses.add(responses.POST, turnstile.VERIFY_URL, json={"success": True})

    form = ReviewForm({**DATA, turnstile.FIELD_NAME: "token"}, remote_ip="203.0.113.9")

    assert form.is_valid(), form.errors
