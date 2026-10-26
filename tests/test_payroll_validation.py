"""Tests for shared payroll input validation."""

from decimal import Decimal

import pytest

from app.services.payroll_validation import PayrollValidationError, validate_payroll_inputs


def test_rejects_leaves_below_zero():
    with pytest.raises(PayrollValidationError) as exc_info:
        validate_payroll_inputs(
            pay_basis="monthly",
            monthly_payment=Decimal("60000"),
            total_leaves=Decimal("-1"),
        )

    assert "total_leaves cannot be negative" in exc_info.value.errors


def test_rejects_leaves_above_30():
    with pytest.raises(PayrollValidationError) as exc_info:
        validate_payroll_inputs(
            pay_basis="monthly",
            monthly_payment=Decimal("60000"),
            total_leaves=Decimal("30.5"),
        )

    assert "total_leaves must be between 0 and 30" in exc_info.value.errors


def test_rejects_negative_extra_work_days_and_comp_off():
    with pytest.raises(PayrollValidationError) as exc_info:
        validate_payroll_inputs(
            pay_basis="monthly",
            monthly_payment=Decimal("60000"),
            extra_work_days=Decimal("-0.5"),
            comp_off=Decimal("-1"),
        )

    assert "extra_work_days cannot be negative" in exc_info.value.errors
    assert "comp_off cannot be negative" in exc_info.value.errors


def test_rejects_non_half_day_increment():
    with pytest.raises(PayrollValidationError) as exc_info:
        validate_payroll_inputs(
            pay_basis="monthly",
            monthly_payment=Decimal("60000"),
            total_leaves=Decimal("1.3"),
            extra_work_days=Decimal("1.3"),
            comp_off=Decimal("1.3"),
        )

    assert "total_leaves must use 0.5 day increments" in exc_info.value.errors
    assert "extra_work_days must use 0.5 day increments" in exc_info.value.errors
    assert "comp_off must use 0.5 day increments" in exc_info.value.errors


@pytest.mark.parametrize("days", [Decimal("0"), Decimal("0.5"), Decimal("1")])
def test_accepts_zero_half_and_whole_days(days):
    result = validate_payroll_inputs(
        pay_basis="monthly",
        monthly_payment=Decimal("60000"),
        total_leaves=days,
        extra_work_days=days,
        comp_off=days,
    )

    assert result["total_leaves"] == days
    assert result["extra_work_days"] == days
    assert result["comp_off"] == days


def test_rejects_comp_off_above_leaves():
    with pytest.raises(PayrollValidationError) as exc_info:
        validate_payroll_inputs(
            pay_basis="monthly",
            monthly_payment=Decimal("60000"),
            total_leaves=Decimal("1"),
            extra_work_days=Decimal("2"),
            comp_off=Decimal("1.5"),
        )

    assert "comp_off cannot exceed total_leaves" in exc_info.value.errors


def test_rejects_comp_off_above_extra_work_days():
    with pytest.raises(PayrollValidationError) as exc_info:
        validate_payroll_inputs(
            pay_basis="monthly",
            monthly_payment=Decimal("60000"),
            total_leaves=Decimal("3"),
            extra_work_days=Decimal("1"),
            comp_off=Decimal("1.5"),
        )

    assert "comp_off cannot exceed extra_work_days" in exc_info.value.errors


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("total_leaves", Decimal("0")),
        ("extra_work_days", Decimal("0")),
        ("comp_off", Decimal("0")),
        ("total_leaves", Decimal("1")),
        ("extra_work_days", Decimal("0.5")),
        ("comp_off", Decimal("0.5")),
    ],
)
def test_rejects_hourly_leave_extra_and_comp_off(field_name, value):
    with pytest.raises(PayrollValidationError) as exc_info:
        validate_payroll_inputs(
            pay_basis="hourly",
            hourly_rate=Decimal("500"),
            hours_worked=Decimal("8"),
            **{field_name: value},
        )

    assert f"{field_name} cannot be set for hourly pay" in exc_info.value.errors


def test_rejects_both_pay_rates():
    with pytest.raises(PayrollValidationError) as exc_info:
        validate_payroll_inputs(
            pay_basis="monthly",
            monthly_payment=Decimal("60000"),
            hourly_rate=Decimal("500"),
        )

    assert "monthly_payment and hourly_rate cannot both be set" in exc_info.value.errors


def test_rejects_missing_monthly_payment():
    with pytest.raises(PayrollValidationError) as exc_info:
        validate_payroll_inputs(pay_basis="monthly")

    assert "monthly_payment is required for monthly pay" in exc_info.value.errors


def test_rejects_missing_hourly_rate():
    with pytest.raises(PayrollValidationError) as exc_info:
        validate_payroll_inputs(pay_basis="hourly", hours_worked=Decimal("8"))

    assert "hourly_rate is required for hourly pay" in exc_info.value.errors


def test_rejects_overtime_amount():
    with pytest.raises(PayrollValidationError) as exc_info:
        validate_payroll_inputs(
            pay_basis="monthly",
            monthly_payment=Decimal("60000"),
            overtime_amount=Decimal("1000"),
        )

    assert "overtime_amount is not an approved overtime method" in exc_info.value.errors
