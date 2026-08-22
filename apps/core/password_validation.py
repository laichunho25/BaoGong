"""Password rules beyond length.

Django ships length, similarity, a common-password list and an all-numeric
check. What it has no opinion on is the character mix, and this platform holds
things a stolen account can spend or damage: a company's public page, a
buyer's requirement with contact details on it, a review that a licensed
company has to live with.

The validator is deliberately one class rather than four. A visitor who is
told "add an upper-case letter", fixes it, and is then told "add a symbol" has
been made to fail twice for one decision; the message below states the whole
requirement once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

#: Anything that is not a letter or a digit counts. An allow-list of symbols
#: would refuse the ones on a mainland keyboard layout for no security gain.
#: ``%%`` because the text is %-formatted with ``min_length`` below, and the
#: literal per-cent sign in the example symbols would otherwise be read as a
#: format specifier.
_HELP: Final = gettext_lazy(
    "密码需同时包含大写字母、小写字母、数字和符号（如 !@#$%%），且不少于 %(min_length)d 位。"
)


class PasswordComplexityValidator:
    """Require an upper-case letter, a lower-case letter, a digit and a symbol.

    ``min_length`` is checked here as well as by Django's own length validator
    so that the single message a visitor reads is complete. The two are
    configured from the same number in settings.
    """

    def __init__(self, min_length: int = 8) -> None:
        self.min_length = min_length

    def validate(self, password: str, user: AbstractBaseUser | None = None) -> None:
        if (
            len(password) >= self.min_length
            and any(c.isupper() for c in password)
            and any(c.islower() for c in password)
            and any(c.isdigit() for c in password)
            and any(not c.isalnum() for c in password)
        ):
            return
        raise ValidationError(
            _(
                "密码需同时包含大写字母、小写字母、数字和符号（如 !@#$%%），"
                "且不少于 %(min_length)d 位。"
            ),
            code="password_not_complex",
            params={"min_length": self.min_length},
        )

    def get_help_text(self) -> str:
        return str(_HELP) % {"min_length": self.min_length}
