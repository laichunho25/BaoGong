"""Cost arithmetic. The budget guard is only as good as this.

CLAUDE.md rule 6: ``Decimal`` throughout, because these numbers are summed
thousands of times a day and a float would drift the guard off its limit.
"""

from __future__ import annotations

from decimal import Decimal

from apps.agents import pricing


def test_a_priced_model_costs_what_the_table_says() -> None:
    cost = pricing.estimate_cost("claude-sonnet-5", input_tokens=1_000_000, output_tokens=0)

    assert cost == Decimal("3.000000")


def test_input_and_output_are_priced_separately() -> None:
    cost = pricing.estimate_cost("claude-sonnet-5", input_tokens=1000, output_tokens=500)

    assert cost == Decimal("0.010500")


def test_the_result_is_a_decimal_at_the_column_s_precision() -> None:
    cost = pricing.estimate_cost("claude-haiku-4-5-20251001", input_tokens=7, output_tokens=3)

    assert isinstance(cost, Decimal)
    assert cost.as_tuple().exponent == -6


def test_an_unpriced_model_is_recorded_rather_than_raised() -> None:
    """Refusing to moderate a review because a price is missing is the wrong
    failure; a name in ``UNPRICED_MODELS`` is how the omission gets noticed."""
    pricing.UNPRICED_MODELS.discard("claude-not-yet-released")

    cost = pricing.estimate_cost("claude-not-yet-released", input_tokens=100, output_tokens=100)

    assert cost == Decimal("0")
    assert "claude-not-yet-released" in pricing.UNPRICED_MODELS


def test_every_model_the_platform_uses_is_priced() -> None:
    """CLAUDE.md section 5 names three models; all three must be in the table,
    or a day's spend silently under-reports."""
    for model in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"):
        assert model in pricing.PRICES_PER_MTOK
