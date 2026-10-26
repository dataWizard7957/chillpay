"""Acceptance tests for the pure payroll engine."""

import inspect
from decimal import Decimal

import pytest

from app.services.payroll_engine import calculate_payroll, prorate_monthly_payment
from app.services.payroll_validation import PayrollValidationError


def _acceptance_inputs(**overrides):
    values = {
        "member_type": "contract",
        "pay_basis": "monthly",
        "monthly_payment": Decimal("60000"),
        "total_leaves": Decimal("3"),
        "extra_work_days": Decimal("2"),
        "comp_off": Decimal("2"),
        "project_allowance_override": Decimal("5000"),
        "project_bonus_override": Decimal("0"),
        "one_off_bonus": Decimal("0"),
        "overtime_hours": Decimal("0"),
    }
    values.update(overrides)
    return values


def test_mandatory_contract_acceptance_example():
    result = calculate_payroll(**_acceptance_inputs())

    assert result.daily_rate == Decimal("2000")
    assert result.final_leaves == Decimal("1")
    assert result.final_extra_days == Decimal("0")
    assert result.final_payment == Decimal("58000.00")
    assert result.project_allowance == Decimal("5000.00")
    assert result.project_bonus == Decimal("0.00")
    assert result.one_off_bonus == Decimal("0.00")
    assert result.overtime_pay == Decimal("0.00")
    assert result.gross_payable == Decimal("63000.00")
    assert result.tds == Decimal("6300.00")
    assert result.amount_paid == Decimal("56700.00")
    assert result.payment_line_label == "Monthly Payment"


def test_zero_comp_off_uses_recorded_leaves_and_extra_days():
    result = calculate_payroll(**_acceptance_inputs(comp_off=Decimal("0")))

    assert result.final_leaves == Decimal("3")
    assert result.final_extra_days == Decimal("2")
    assert result.final_payment == Decimal("58000.00")
    assert result.gross_payable == Decimal("63000.00")
    assert result.tds == Decimal("6300.00")
    assert result.amount_paid == Decimal("56700.00")


def test_half_day_leave():
    result = calculate_payroll(
        member_type="contract",
        pay_basis="monthly",
        monthly_payment=Decimal("60000"),
        total_leaves=Decimal("0.5"),
    )

    assert result.final_leaves == Decimal("0.5")
    assert result.final_extra_days == Decimal("0")
    assert result.final_payment == Decimal("59000.00")


def test_invalid_comp_off_is_rejected():
    with pytest.raises(PayrollValidationError):
        calculate_payroll(**_acceptance_inputs(comp_off=Decimal("3")))


def test_missing_allowance_override_uses_profile_default():
    result = calculate_payroll(
        **_acceptance_inputs(
            default_project_allowance=Decimal("5000"),
            project_allowance_override=None,
        )
    )

    assert result.project_allowance == Decimal("5000.00")
    assert result.gross_payable == Decimal("63000.00")


def test_explicit_zero_allowance_and_bonus_overrides():
    result = calculate_payroll(
        **_acceptance_inputs(
            default_project_allowance=Decimal("5000"),
            project_allowance_override=Decimal("0"),
            default_project_bonus=Decimal("2000"),
            project_bonus_override=Decimal("0"),
        )
    )

    assert result.project_allowance == Decimal("0.00")
    assert result.project_bonus == Decimal("0.00")
    assert result.gross_payable == Decimal("58000.00")
    assert result.tds == Decimal("5800.00")


def test_allowance_and_bonus_overrides_replace_defaults():
    result = calculate_payroll(
        **_acceptance_inputs(
            default_project_allowance=Decimal("5000"),
            project_allowance_override=Decimal("1500"),
            default_project_bonus=Decimal("2000"),
            project_bonus_override=Decimal("250"),
        )
    )

    assert result.project_allowance == Decimal("1500.00")
    assert result.project_bonus == Decimal("250.00")
    assert result.gross_payable == Decimal("59750.00")


def test_one_off_bonus_is_included_once_in_gross_and_contract_tds():
    result = calculate_payroll(**_acceptance_inputs(one_off_bonus=Decimal("1000")))

    assert result.one_off_bonus == Decimal("1000.00")
    assert result.gross_payable == Decimal("64000.00")
    assert result.tds == Decimal("6400.00")
    assert result.gross_payable == (
        result.final_payment
        + result.overtime_pay
        + result.project_allowance
        + result.project_bonus
        + result.one_off_bonus
    )


def test_employee_and_trainee_tds_are_zero():
    employee = calculate_payroll(**_acceptance_inputs(member_type="employee"))
    trainee = calculate_payroll(**_acceptance_inputs(member_type="Trainee"))

    assert employee.tds == Decimal("0.00")
    assert employee.amount_paid == Decimal("63000.00")
    assert employee.payment_line_label == "Monthly Payment"
    assert trainee.tds == Decimal("0.00")
    assert trainee.amount_paid == Decimal("63000.00")
    assert trainee.payment_line_label == "Stipend"


def test_contract_tds_rate_is_configurable():
    result = calculate_payroll(**_acceptance_inputs(contract_tds_rate=Decimal("0.05")))

    assert result.tds == Decimal("3150.00")
    assert result.amount_paid == Decimal("59850.00")


def test_hourly_regular_pay_and_overtime_stay_separate():
    result = calculate_payroll(
        member_type="contract",
        pay_basis="hourly",
        hourly_rate=Decimal("500"),
        hours_worked=Decimal("10"),
        overtime_hours=Decimal("2"),
    )

    assert result.regular_pay == Decimal("5000.00")
    assert result.overtime_pay == Decimal("1500.00")
    assert result.gross_payable == Decimal("6500.00")
    assert result.tds == Decimal("650.00")
    assert result.final_payment is None
    assert result.payment_line_label == "Regular Pay"


def test_hourly_employee_has_no_tds_and_honors_overtime_multiplier():
    result = calculate_payroll(
        member_type="employee",
        pay_basis="hourly",
        hourly_rate=Decimal("500"),
        hours_worked=Decimal("10"),
        overtime_hours=Decimal("2"),
        overtime_multiplier=Decimal("2"),
    )

    assert result.regular_pay == Decimal("5000.00")
    assert result.overtime_pay == Decimal("2000.00")
    assert result.gross_payable == Decimal("7000.00")
    assert result.tds == Decimal("0.00")


def test_monthly_overtime_uses_hourly_equivalent():
    result = calculate_payroll(
        member_type="employee",
        pay_basis="monthly",
        monthly_payment=Decimal("60000"),
        overtime_hours=Decimal("2"),
    )

    assert result.hourly_equivalent == Decimal("250")
    assert result.overtime_pay == Decimal("750.00")
    assert result.final_payment == Decimal("60000.00")
    assert result.gross_payable == Decimal("60750.00")
    assert result.tds == Decimal("0.00")


def test_monthly_overtime_multiplier_is_configurable():
    result = calculate_payroll(
        member_type="contract",
        pay_basis="monthly",
        monthly_payment=Decimal("60000"),
        overtime_hours=Decimal("2"),
        overtime_multiplier=Decimal("2"),
    )

    assert result.overtime_pay == Decimal("1000.00")


def test_pay_components_are_not_added_twice():
    result = calculate_payroll(
        member_type="contract",
        pay_basis="monthly",
        monthly_payment=Decimal("60000"),
        project_allowance_override=Decimal("5000"),
        project_bonus_override=Decimal("2000"),
        one_off_bonus=Decimal("1000"),
        overtime_hours=Decimal("2"),
    )

    expected_gross = (
        result.final_payment
        + result.overtime_pay
        + result.project_allowance
        + result.project_bonus
        + result.one_off_bonus
    )
    assert result.project_allowance == Decimal("5000.00")
    assert result.project_bonus == Decimal("2000.00")
    assert result.one_off_bonus == Decimal("1000.00")
    assert result.overtime_pay == Decimal("750.00")
    assert result.gross_payable == expected_gross
    assert result.gross_payable == Decimal("68750.00")


def test_partial_advance_recovery_is_deducted_after_tds():
    result = calculate_payroll(**_acceptance_inputs(actual_advance_recovery=Decimal("1000")))

    assert result.tds == Decimal("6300.00")
    assert result.amount_paid == Decimal("56700.00")
    assert result.actual_advance_recovery == Decimal("1000.00")
    assert result.recovery_shortfall == Decimal("0.00")
    assert result.recovery_shortfall_flag is False
    assert result.net_deposit == Decimal("55700.00")


def test_insufficient_recovery_flags_shortfall_without_negative_net():
    result = calculate_payroll(**_acceptance_inputs(actual_advance_recovery=Decimal("60000")))

    assert result.amount_paid == Decimal("56700.00")
    assert result.actual_advance_recovery == Decimal("56700.00")
    assert result.recovery_shortfall == Decimal("3300.00")
    assert result.recovery_shortfall_flag is True
    assert result.net_deposit == Decimal("0.00")


def test_proration_is_isolated_from_the_monthly_formula():
    assert prorate_monthly_payment(Decimal("60000"), Decimal("15")) == Decimal("30000.00")
    assert "notice" not in inspect.signature(prorate_monthly_payment).parameters

    result = calculate_payroll(**_acceptance_inputs())
    assert result.final_payment == Decimal("58000.00")


def test_negative_proration_inputs_are_rejected():
    with pytest.raises(PayrollValidationError):
        prorate_monthly_payment(Decimal("60000"), Decimal("-1"))
