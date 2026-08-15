"""Per-model token prices, in USD.

CLAUDE.md rule 4 asks every run to record its cost, and rule 6 says money is
``Decimal``. The two together mean the price table cannot be a float dict read
off a blog post - it is the input to the daily budget guard, and a budget guard
built on rounding error is a budget guard that lets an incident run all night.

Prices are per **million** tokens and are checked against Anthropic's published
list. An unknown model costs nothing here rather than raising: refusing to
answer a review because a price is missing would be the wrong failure, but a
zero in the ledger is visible, so ``UNPRICED_MODELS`` is what monitoring looks
at.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

#: model -> (input per 1M tokens, output per 1M tokens)
PRICES_PER_MTOK: Final[dict[str, tuple[Decimal, Decimal]]] = {
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5": (Decimal("3.00"), Decimal("15.00")),
    "claude-haiku-4-5-20251001": (Decimal("1.00"), Decimal("5.00")),
}

_MILLION = Decimal(1_000_000)
#: cost_usd is DecimalField(max_digits=10, decimal_places=6); quantise here so
#: the database is never the thing that decides how a price gets rounded.
_CENTS = Decimal("0.000001")

#: Models seen at runtime with no entry above. Populated by ``estimate_cost``
#: so a model upgrade that forgets this table shows up as a name rather than as
#: a silently shrinking spend figure.
UNPRICED_MODELS: set[str] = set()


def estimate_cost(model: str, *, input_tokens: int, output_tokens: int) -> Decimal:
    """Cost of one call, rounded to the six decimal places the column stores."""
    price = PRICES_PER_MTOK.get(model)
    if price is None:
        UNPRICED_MODELS.add(model)
        return Decimal("0")
    input_price, output_price = price
    cost = (Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price) / _MILLION
    return cost.quantize(_CENTS)
