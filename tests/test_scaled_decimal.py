"""Exact decimal storage. Extra places are rejected, never rounded."""

from decimal import Decimal

import pytest

from app.db.types import ScaledDecimal


def test_money_scale_stores_two_decimal_places_as_integer():
    money = ScaledDecimal(2)

    assert money.process_bind_param(Decimal("60000"), None) == 6_000_000
    assert money.process_bind_param(Decimal("12.34"), None) == 1234
    assert money.process_bind_param("12.30", None) == 1230
    assert money.process_bind_param(12, None) == 1200
    assert money.process_result_value(1234, None) == Decimal("12.34")
    assert money.process_bind_param(None, None) is None
    assert money.process_result_value(None, None) is None


def test_days_scale_stores_one_decimal_place_as_integer():
    days = ScaledDecimal(1)

    assert days.process_bind_param(Decimal("1.5"), None) == 15
    assert days.process_bind_param(Decimal("30"), None) == 300
    assert days.process_result_value(15, None) == Decimal("1.5")


def test_scaled_decimal_rejects_extra_decimal_places():
    money = ScaledDecimal(2)
    days = ScaledDecimal(1)

    with pytest.raises(ValueError, match="decimal places"):
        money.process_bind_param(Decimal("12.345"), None)
    with pytest.raises(ValueError, match="decimal places"):
        days.process_bind_param(Decimal("1.25"), None)


def test_scaled_decimal_rejects_float_and_bool():
    money = ScaledDecimal(2)

    with pytest.raises(TypeError, match="Decimal, int, or str"):
        money.process_bind_param(12.34, None)
    with pytest.raises(TypeError, match="Decimal, int, or str"):
        money.process_bind_param(True, None)
