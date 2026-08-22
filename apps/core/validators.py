"""Field validators shared across the forms.

Kept here rather than in one app's ``forms.py`` because the same question is
asked in three places: the account's own number, the number on a claim, and
the number a company publishes on its page. A number that is accepted in one
shape on one form and another shape on the next is a number nobody can dial.
"""

from __future__ import annotations

import re
from typing import Final

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

#: Separators a person types out of habit. They carry no information once the
#: digits are known, so they are removed rather than refused - refusing them
#: teaches nothing and only costs the visitor a second attempt.
_SEPARATORS: Final = re.compile(r"[\s\-().]")

#: An optional country code, then digits. The lower bound is 6 because Hong
#: Kong landlines were 6 digits within living memory and some legacy numbers
#: are still printed that way; the upper bound is the E.164 maximum of 15,
#: plus room for an extension somebody appends.
_PHONE = re.compile(r"^\+?\d{6,20}$")

#: For the widget, so a phone keypad opens on a handset and the browser
#: refuses the obvious mistakes before the round trip.
PHONE_INPUT_ATTRS: Final[dict[str, str]] = {
    "inputmode": "tel",
    "pattern": r"[0-9+\s\-()]*",
    "placeholder": "+8613800138000",
}


def normalise_phone(value: str) -> str:
    """Strip the decorations a person types, keeping ``+`` and the digits."""
    return _SEPARATORS.sub("", (value or "").strip())


def validate_phone(value: str) -> None:
    """Accept digits only, with an optional leading ``+``.

    A phone number is a numeric field wearing a text field's clothes: the
    column has to stay dialable, and letters in it are either a typo or
    somebody using the box for something else. The country code is allowed
    because most of the people typing here are dialling from the mainland into
    Hong Kong, and a number without it is not reachable.
    """
    if not value:
        return
    if not _PHONE.match(normalise_phone(value)):
        raise ValidationError(
            _("请只填写数字，可用 + 开头的国际区号，例如 +8613800138000。"),
            code="invalid_phone",
        )
