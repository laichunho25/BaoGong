"""Phone numbers: digits, and the decorations people type around them."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.core.validators import normalise_phone, validate_phone


@pytest.mark.parametrize(
    "value",
    ["91234567", "+85291234567", "+852 9123 4567", "(852) 9123-4567", "+86 138 0013 8000"],
)
def test_a_dialable_number_is_accepted_however_it_is_punctuated(value: str) -> None:
    validate_phone(value)


@pytest.mark.parametrize(
    "value",
    ["call me", "9123 4567 ext. 12", "+852-9123-4567x", "微信 abc123", "12345"],
)
def test_anything_that_is_not_a_number_is_refused(value: str) -> None:
    with pytest.raises(ValidationError):
        validate_phone(value)


def test_an_empty_value_is_left_alone() -> None:
    """The field is optional on every form that uses it; a required field says
    so with ``required``, not by way of the validator."""
    validate_phone("")


def test_normalising_keeps_the_country_code_and_drops_the_rest() -> None:
    assert normalise_phone(" +852 (9123) 4567 ") == "+85291234567"
