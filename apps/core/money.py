"""Money handling.

CLAUDE.md rule 6: amounts are stored as ``currency`` (ISO-4217 alpha-3) plus
``amount_minor`` (integer, smallest unit). Floats are never used anywhere in
the money path; conversions go through ``Decimal``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

# Number of decimal digits in the currency's minor unit (ISO 4217 exponent).
# Only currencies the platform actually quotes in are listed; anything else
# raises rather than silently assuming 2.
MINOR_UNIT_EXPONENT: Final[dict[str, int]] = {
    "HKD": 2,
    "CNY": 2,
    "USD": 2,
    "SGD": 2,
    "EUR": 2,
    "GBP": 2,
    "JPY": 0,
}


class MoneyError(ValueError):
    """Raised for unsupported currencies or invalid amounts."""


def _exponent(currency: str) -> int:
    try:
        return MINOR_UNIT_EXPONENT[currency]
    except KeyError:
        raise MoneyError(f"Unsupported currency: {currency!r}") from None


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """An immutable amount in a single currency, held in minor units."""

    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount_minor, int) or isinstance(self.amount_minor, bool):
            raise MoneyError("amount_minor must be an int (minor units)")
        object.__setattr__(self, "currency", self.currency.upper())
        _exponent(self.currency)

    # ------------------------------------------------------------ construction

    @classmethod
    def from_decimal(cls, amount: Decimal | str | int, currency: str) -> Money:
        """Build from a major-unit amount, e.g. ``Money.from_decimal("1200.50", "HKD")``.

        The amount must be exactly representable in the currency's minor unit;
        a value with excess precision raises rather than rounding silently.
        """
        currency = currency.upper()
        exponent = _exponent(currency)
        try:
            value = Decimal(amount)
        except (InvalidOperation, TypeError) as exc:
            raise MoneyError(f"Not a valid decimal amount: {amount!r}") from exc

        scaled = value.scaleb(exponent)
        if scaled != scaled.to_integral_value():
            raise MoneyError(
                f"{value} has more precision than {currency} supports ({exponent} decimal places)"
            )
        return cls(amount_minor=int(scaled), currency=currency)

    # ------------------------------------------------------------ conversion

    def to_decimal(self) -> Decimal:
        """Return the major-unit amount as a ``Decimal``."""
        return Decimal(self.amount_minor).scaleb(-_exponent(self.currency))

    def format(self) -> str:
        """Human-readable form, e.g. ``HKD 1,200.50``. Not localised yet."""
        exponent = _exponent(self.currency)
        return f"{self.currency} {self.to_decimal():,.{exponent}f}"

    # ------------------------------------------------------------ arithmetic

    def _check_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise MoneyError(f"Cannot combine {self.currency} with {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check_same_currency(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check_same_currency(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def __mul__(self, factor: int) -> Money:
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise MoneyError("Money can only be multiplied by an int")
        return Money(self.amount_minor * factor, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._check_same_currency(other)
        return self.amount_minor < other.amount_minor

    def __le__(self, other: Money) -> bool:
        self._check_same_currency(other)
        return self.amount_minor <= other.amount_minor

    def __str__(self) -> str:
        return self.format()


def sum_money(items: list[Money], currency: str) -> Money:
    """Total a list of Money, returning a zero amount when the list is empty."""
    total = Money(0, currency)
    for item in items:
        total = total + item
    return total
