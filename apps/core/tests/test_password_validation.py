"""The character-mix rule.

Length, the common-password list and the all-numeric check are Django's and
are tested by Django. What is tested here is the rule this project added, and
in particular that it says the whole requirement in one message rather than
sending somebody round the loop four times.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.core.password_validation import PasswordComplexityValidator


@pytest.mark.parametrize(
    "password",
    [
        "Correct-Horse9!",
        "aB3$aB3$aB",
        "Tr0ub4dor&3xy",
    ],
)
def test_a_mixed_password_of_full_length_is_accepted(password: str) -> None:
    PasswordComplexityValidator(min_length=10).validate(password)


@pytest.mark.parametrize(
    ("password", "missing"),
    [
        ("correct-horse9!", "upper case"),
        ("CORRECT-HORSE9!", "lower case"),
        ("Correct-Horsey!", "digit"),
        ("CorrectHorse99", "symbol"),
        ("Ab3$xyz", "length"),
    ],
)
def test_anything_missing_is_refused(password: str, missing: str) -> None:
    with pytest.raises(ValidationError) as caught:
        PasswordComplexityValidator(min_length=10).validate(password)

    assert caught.value.code == "password_not_complex"


def test_one_message_states_the_whole_requirement() -> None:
    """A visitor told "add an upper-case letter", who fixes it and is then told
    "add a symbol", has been made to fail twice for one decision."""
    with pytest.raises(ValidationError) as caught:
        PasswordComplexityValidator(min_length=10).validate("password")

    message = caught.value.messages[0]
    assert "大写字母" in message
    assert "符号" in message
    assert "10" in message


def test_the_help_text_carries_the_configured_length() -> None:
    assert "12" in PasswordComplexityValidator(min_length=12).get_help_text()
