"""Pure payroll calculations.

All money uses Decimal. Final monetary amounts are rounded to two decimal
places with ROUND_HALF_UP. Daily rates and other intermediate quotients stay
exact until a final amount is produced. This module does not read current
member records and does not apply mid-month proration inside the monthly formula.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.services.payroll_validation import PayrollValidationError, validate_payroll_inputs

MONEY_PLACES = Decimal("0.01")
DAYS_IN_PAY_PERIOD = Decimal("30")
HOURS_PER_DAY = Decimal("8")
DEFAULT_OVERTIME_MULTIPLIER = Decimal("1.5")
DEFAULT_CONTRACT_TDS_RATE = Decimal("0.10")
ZERO = Decimal("0")
ZERO_MONEY = Decimal("0.00")

MEMBER_CONTRACT = "contract"
MEMBER_EMPLOYEE = "employee"
MEMBER_TRAINEE = "trainee"
MEMBER_TYPES = (MEMBER_CONTRACT, MEMBER_EMPLOYEE, MEMBER_TRAINEE)


@dataclass(frozen=True)
class PayrollCalculation:
    """One calculated payroll result. Monetary fields are two-decimal Decimals."""

    member_type: str
    pay_basis: str
    daily_rate: Decimal | None
    hourly_equivalent: Decimal | None
    final_leaves: Decimal | None
    final_extra_days: Decimal | None
    final_payment: Decimal | None
    regular_pay: Decimal | None
    overtime_pay: Decimal
    project_allowance: Decimal
    project_bonus: Decimal
    one_off_bonus: Decimal
    gross_payable: Decimal
    tds: Decimal
    amount_paid: Decimal
    actual_advance_recovery: Decimal
    recovery_shortfall: Decimal
    recovery_shortfall_flag: bool
    net_deposit: Decimal
    payment_line_label: str


def calculate_payroll(
    *,
    member_type: str,
    pay_basis: str,
    monthly_payment=None,
    hourly_rate=None,
    total_leaves=None,
    extra_work_days=None,
    comp_off=None,
    hours_worked=None,
    overtime_hours=None,
    overtime_multiplier=None,
    default_project_allowance=None,
    project_allowance_override=None,
    default_project_bonus=None,
    project_bonus_override=None,
    one_off_bonus=None,
    contract_tds_rate=None,
    actual_advance_recovery=None,
) -> PayrollCalculation:
    """Calculate payroll from explicit inputs.

    A missing allowance or bonus override uses the profile default. An override
    of zero stays zero. Pass the actual amount recovered, not a scheduled installment.
    """

    normalized_type = _member_type(member_type)
    validated = validate_payroll_inputs(
        pay_basis=pay_basis,
        monthly_payment=monthly_payment,
        hourly_rate=hourly_rate,
        total_leaves=total_leaves,
        extra_work_days=extra_work_days,
        comp_off=comp_off,
        hours_worked=hours_worked,
        overtime_hours=overtime_hours,
    )
    multiplier = _required_non_negative(
        DEFAULT_OVERTIME_MULTIPLIER if overtime_multiplier is None else overtime_multiplier,
        "overtime_multiplier",
    )
    tds_rate = _required_non_negative(
        DEFAULT_CONTRACT_TDS_RATE if contract_tds_rate is None else contract_tds_rate,
        "contract_tds_rate",
    )
    allowance = _component_amount(
        default_project_allowance,
        project_allowance_override,
        "project_allowance",
    )
    bonus = _component_amount(default_project_bonus, project_bonus_override, "project_bonus")
    one_off = _required_non_negative(ZERO if one_off_bonus is None else one_off_bonus, "one_off_bonus")
    requested_recovery = _required_non_negative(
        ZERO if actual_advance_recovery is None else actual_advance_recovery,
        "actual_advance_recovery",
    )

    daily_rate = None
    hourly_equivalent = None
    final_leaves = None
    final_extra_days = None
    final_payment = None
    regular_pay = None

    if validated["pay_basis"] == "monthly":
        monthly = validated["monthly_payment"]
        daily_rate = monthly / DAYS_IN_PAY_PERIOD
        hourly_equivalent = daily_rate / HOURS_PER_DAY
        final_leaves = validated["total_leaves"] - validated["comp_off"]
        final_extra_days = validated["extra_work_days"] - validated["comp_off"]
        final_payment = _money(
            monthly - (final_leaves * daily_rate) + (final_extra_days * daily_rate)
        )
        overtime_pay = _money(validated["overtime_hours"] * hourly_equivalent * multiplier)
        payment_line_label = "Stipend" if normalized_type == MEMBER_TRAINEE else "Monthly Payment"
        pay_before_additions = final_payment
    else:
        regular_pay = _money(validated["hours_worked"] * validated["hourly_rate"])
        overtime_pay = _money(validated["overtime_hours"] * validated["hourly_rate"] * multiplier)
        payment_line_label = "Regular Pay"
        pay_before_additions = regular_pay

    allowance = _money(allowance)
    bonus = _money(bonus)
    one_off = _money(one_off)
    overtime_pay = _money(overtime_pay)
    gross_payable = _money(pay_before_additions + overtime_pay + allowance + bonus + one_off)

    if normalized_type == MEMBER_CONTRACT:
        tds = _money(gross_payable * tds_rate)
    else:
        tds = ZERO_MONEY

    amount_paid = _money(gross_payable - tds)
    if requested_recovery > amount_paid:
        recovered = amount_paid
        shortfall = _money(requested_recovery - amount_paid)
        shortfall_flag = True
    else:
        recovered = _money(requested_recovery)
        shortfall = ZERO_MONEY
        shortfall_flag = False
    net_deposit = _money(amount_paid - recovered)

    return PayrollCalculation(
        member_type=normalized_type,
        pay_basis=validated["pay_basis"],
        daily_rate=daily_rate,
        hourly_equivalent=hourly_equivalent,
        final_leaves=final_leaves,
        final_extra_days=final_extra_days,
        final_payment=final_payment,
        regular_pay=regular_pay,
        overtime_pay=overtime_pay,
        project_allowance=allowance,
        project_bonus=bonus,
        one_off_bonus=one_off,
        gross_payable=gross_payable,
        tds=tds,
        amount_paid=amount_paid,
        actual_advance_recovery=recovered,
        recovery_shortfall=shortfall,
        recovery_shortfall_flag=shortfall_flag,
        net_deposit=net_deposit,
        payment_line_label=payment_line_label,
    )


def prorate_monthly_payment(monthly_payment, eligible_days) -> Decimal:
    """Return Monthly Payment × Eligible Days / 30.

    Eligible days must be supplied by the caller. This function does not decide
    whether a joining date or last working date is payable, and it is not used
    by calculate_payroll.
    """

    payment = _required_non_negative(monthly_payment, "monthly_payment")
    days = _required_non_negative(eligible_days, "eligible_days")
    return _money(payment * days / DAYS_IN_PAY_PERIOD)


def _member_type(member_type: str) -> str:
    if not isinstance(member_type, str):
        raise PayrollValidationError(["member_type must be contract, employee, or trainee"])
    normalized = member_type.casefold()
    if normalized not in MEMBER_TYPES:
        raise PayrollValidationError(["member_type must be contract, employee, or trainee"])
    return normalized


def _component_amount(default_amount, override, field_name: str) -> Decimal:
    if override is None:
        selected = ZERO if default_amount is None else default_amount
    else:
        selected = override
    return _required_non_negative(selected, field_name)


def _required_non_negative(value, field_name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise PayrollValidationError([f"{field_name} must be a decimal value"])
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, str):
        try:
            amount = Decimal(value)
        except Exception as exc:
            raise PayrollValidationError([f"{field_name} must be a decimal value"]) from exc
    else:
        raise PayrollValidationError([f"{field_name} must be a decimal value"])
    if amount < ZERO:
        raise PayrollValidationError([f"{field_name} cannot be negative"])
    return amount


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)
