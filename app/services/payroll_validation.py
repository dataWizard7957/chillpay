"""Shared validation for payroll inputs.

Calculation lives in the payroll engine. This module accepts or rejects
inputs before any money is calculated.
"""

from decimal import Decimal

ZERO = Decimal("0")
MAX_TOTAL_LEAVES = Decimal("30")
PAY_BASIS_MONTHLY = "monthly"
PAY_BASIS_HOURLY = "hourly"


class PayrollValidationError(ValueError):
    """Raised when payroll inputs break a stated validation rule."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def validate_payroll_inputs(
    *,
    pay_basis: str,
    monthly_payment=None,
    hourly_rate=None,
    total_leaves=None,
    extra_work_days=None,
    comp_off=None,
    hours_worked=None,
    overtime_hours=None,
    overtime_amount=None,
) -> dict:
    """Validate one payroll input set and return normalized values.

    Omitted monthly leave, extra-day, and comp-off values become zero.
    Those fields must be omitted for hourly pay. An overtime amount is
    rejected because only hour-based overtime is implemented.
    """

    errors: list[str] = []

    if pay_basis not in (PAY_BASIS_MONTHLY, PAY_BASIS_HOURLY):
        errors.append("pay_basis must be monthly or hourly")

    monthly_amount = _optional_decimal(monthly_payment, "monthly_payment", errors)
    hourly_amount = _optional_decimal(hourly_rate, "hourly_rate", errors)
    leaves = _optional_decimal(total_leaves, "total_leaves", errors)
    extra_days = _optional_decimal(extra_work_days, "extra_work_days", errors)
    comp_off_days = _optional_decimal(comp_off, "comp_off", errors)
    worked_hours = _optional_decimal(hours_worked, "hours_worked", errors)
    overtime = _optional_decimal(overtime_hours, "overtime_hours", errors)

    if overtime_amount is not None:
        errors.append("overtime_amount is not an approved overtime method")

    both_rates = monthly_amount is not None and hourly_amount is not None
    if both_rates:
        errors.append("monthly_payment and hourly_rate cannot both be set")

    if pay_basis == PAY_BASIS_MONTHLY:
        if monthly_amount is None:
            errors.append("monthly_payment is required for monthly pay")
        if hourly_amount is not None and not both_rates:
            errors.append("hourly_rate cannot be set for monthly pay")
        if worked_hours is not None:
            errors.append("hours_worked cannot be set for monthly pay")
        leaves = ZERO if leaves is None else leaves
        extra_days = ZERO if extra_days is None else extra_days
        comp_off_days = ZERO if comp_off_days is None else comp_off_days
        _check_day_quantity(leaves, "total_leaves", errors, maximum=MAX_TOTAL_LEAVES)
        _check_day_quantity(extra_days, "extra_work_days", errors)
        _check_day_quantity(comp_off_days, "comp_off", errors)
        if leaves is not None and comp_off_days is not None and comp_off_days > leaves:
            errors.append("comp_off cannot exceed total_leaves")
        if extra_days is not None and comp_off_days is not None and comp_off_days > extra_days:
            errors.append("comp_off cannot exceed extra_work_days")
    elif pay_basis == PAY_BASIS_HOURLY:
        if hourly_amount is None:
            errors.append("hourly_rate is required for hourly pay")
        if monthly_amount is not None and not both_rates:
            errors.append("monthly_payment cannot be set for hourly pay")
        if leaves is not None:
            errors.append("total_leaves cannot be set for hourly pay")
        if extra_days is not None:
            errors.append("extra_work_days cannot be set for hourly pay")
        if comp_off_days is not None:
            errors.append("comp_off cannot be set for hourly pay")
        if worked_hours is None:
            errors.append("hours_worked is required for hourly pay")

    _check_non_negative(monthly_amount, "monthly_payment", errors)
    _check_non_negative(hourly_amount, "hourly_rate", errors)
    _check_non_negative(worked_hours, "hours_worked", errors)
    _check_non_negative(overtime, "overtime_hours", errors)

    if errors:
        raise PayrollValidationError(errors)

    return {
        "pay_basis": pay_basis,
        "monthly_payment": monthly_amount,
        "hourly_rate": hourly_amount,
        "total_leaves": leaves,
        "extra_work_days": extra_days,
        "comp_off": comp_off_days,
        "hours_worked": worked_hours,
        "overtime_hours": ZERO if overtime is None else overtime,
    }


def _optional_decimal(value, field_name: str, errors: list[str]):
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, float):
        errors.append(f"{field_name} must be a decimal value")
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception:
            errors.append(f"{field_name} must be a decimal value")
            return None
    errors.append(f"{field_name} must be a decimal value")
    return None


def _check_day_quantity(value: Decimal, field_name: str, errors: list[str], maximum: Decimal | None = None) -> None:
    if value < ZERO:
        errors.append(f"{field_name} cannot be negative")
        return
    if maximum is not None and value > maximum:
        errors.append(f"{field_name} must be between 0 and {maximum}")
        return
    if (value * 2) != (value * 2).to_integral_value():
        errors.append(f"{field_name} must use 0.5 day increments")


def _check_non_negative(value, field_name: str, errors: list[str]) -> None:
    if value is not None and value < ZERO:
        errors.append(f"{field_name} cannot be negative")
