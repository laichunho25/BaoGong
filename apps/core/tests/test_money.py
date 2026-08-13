from decimal import Decimal

import pytest

from apps.core.money import Money, MoneyError, sum_money


class TestConstruction:
    def test_from_decimal_string(self):
        assert Money.from_decimal("1200.50", "HKD") == Money(120050, "HKD")

    def test_from_decimal_accepts_int_major_units(self):
        assert Money.from_decimal(1200, "HKD").amount_minor == 120000

    def test_currency_is_normalised_to_upper(self):
        assert Money(100, "hkd").currency == "HKD"

    def test_zero_decimal_currency(self):
        assert Money.from_decimal("1200", "JPY") == Money(1200, "JPY")

    def test_rejects_excess_precision_instead_of_rounding(self):
        with pytest.raises(MoneyError, match="more precision"):
            Money.from_decimal("10.005", "HKD")

    def test_rejects_unknown_currency(self):
        with pytest.raises(MoneyError, match="Unsupported currency"):
            Money(100, "XYZ")

    def test_rejects_float_amount_minor(self):
        with pytest.raises(MoneyError, match="must be an int"):
            Money(100.5, "HKD")  # type: ignore[arg-type]

    def test_rejects_bool_amount_minor(self):
        with pytest.raises(MoneyError):
            Money(True, "HKD")  # type: ignore[arg-type]

    def test_rejects_garbage_amount(self):
        with pytest.raises(MoneyError, match="valid decimal"):
            Money.from_decimal("abc", "HKD")


class TestConversion:
    def test_to_decimal_round_trips(self):
        assert Money(120050, "HKD").to_decimal() == Decimal("1200.50")

    def test_to_decimal_returns_decimal_not_float(self):
        assert isinstance(Money(1, "HKD").to_decimal(), Decimal)

    def test_format(self):
        assert Money(120050, "HKD").format() == "HKD 1,200.50"
        assert Money(1200, "JPY").format() == "JPY 1,200"

    def test_str_uses_format(self):
        assert str(Money(120050, "HKD")) == "HKD 1,200.50"


class TestArithmetic:
    def test_add_and_subtract(self):
        a, b = Money(1000, "HKD"), Money(250, "HKD")
        assert (a + b).amount_minor == 1250
        assert (a - b).amount_minor == 750

    def test_multiply_by_int(self):
        assert (Money(1000, "HKD") * 3).amount_minor == 3000

    def test_multiply_by_float_is_rejected(self):
        with pytest.raises(MoneyError, match="only be multiplied by an int"):
            Money(1000, "HKD") * 1.5  # type: ignore[operator]

    def test_mixing_currencies_is_rejected(self):
        with pytest.raises(MoneyError, match="Cannot combine"):
            Money(1000, "HKD") + Money(1000, "USD")

    def test_comparison(self):
        assert Money(100, "HKD") < Money(200, "HKD")
        assert Money(100, "HKD") <= Money(100, "HKD")

    def test_comparison_across_currencies_is_rejected(self):
        with pytest.raises(MoneyError):
            _ = Money(100, "HKD") < Money(100, "USD")

    def test_is_immutable(self):
        with pytest.raises(Exception):  # noqa: B017 - frozen dataclass
            Money(100, "HKD").amount_minor = 200  # type: ignore[misc]


class TestSumMoney:
    def test_empty_list_gives_zero_in_requested_currency(self):
        assert sum_money([], "HKD") == Money(0, "HKD")

    def test_totals_items(self):
        items = [Money(1000, "HKD"), Money(2500, "HKD"), Money(1, "HKD")]
        assert sum_money(items, "HKD") == Money(3501, "HKD")
