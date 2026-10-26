"""Payroll preview and frozen snapshots.

The payroll engine performs the calculation. Hourly entries ignore the zero
values stored in the required day columns. Scheduled advance installments are
not deducted unless a recovery row exists.
"""

import json
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AdvanceRecovery, AppSetting, PayTerm, PayrollEntry, PayrollSnapshot, User
from app.services.errors import ServiceError
from app.services.payroll_engine import (
    DEFAULT_CONTRACT_TDS_RATE,
    DEFAULT_OVERTIME_MULTIPLIER,
    PayrollCalculation,
    calculate_payroll,
)
from app.services.payroll_entries import _term_for_full_month

MONEY = Decimal("0.01")


def preview_entry(db: Session, entry: PayrollEntry) -> tuple[PayrollCalculation, PayTerm, dict]:
    term = _term_for_full_month(db, entry.member_id, entry.year, entry.month)
    hourly = term.pay_basis == "hourly"
    inputs = {
        "pay_term_id": term.id,
        "pay_basis": term.pay_basis,
        "monthly_payment": _plain(term.monthly_payment),
        "hourly_rate": _plain(term.hourly_rate),
        "total_leaves": None if hourly else _plain(entry.total_leaves),
        "extra_work_days": None if hourly else _plain(entry.extra_work_days),
        "comp_off": None if hourly else _plain(entry.comp_off),
        "hours_worked": _plain(entry.hours_worked),
        "overtime_hours": _plain(entry.overtime_hours),
        "default_project_allowance": _plain(term.default_project_allowance),
        "project_allowance_override": _plain(entry.project_allowance_override),
        "default_project_bonus": _plain(term.default_project_bonus),
        "project_bonus_override": _plain(entry.project_bonus_override),
        "one_off_bonus": _plain(entry.one_off_bonus),
        "overtime_multiplier": _plain(_setting(db, "overtime_multiplier", DEFAULT_OVERTIME_MULTIPLIER)),
        "contract_tds_rate": _plain(_setting(db, "contract_tds_rate", DEFAULT_CONTRACT_TDS_RATE)),
        "actual_advance_recovery": _plain(_recovered(db, entry.id)),
    }
    result = calculate_payroll(
        member_type=_member_type(db, entry),
        pay_basis=term.pay_basis,
        monthly_payment=term.monthly_payment,
        hourly_rate=term.hourly_rate,
        total_leaves=None if hourly else entry.total_leaves,
        extra_work_days=None if hourly else entry.extra_work_days,
        comp_off=None if hourly else entry.comp_off,
        hours_worked=entry.hours_worked,
        overtime_hours=entry.overtime_hours,
        overtime_multiplier=_setting(db, "overtime_multiplier", DEFAULT_OVERTIME_MULTIPLIER),
        default_project_allowance=term.default_project_allowance,
        project_allowance_override=entry.project_allowance_override,
        default_project_bonus=term.default_project_bonus,
        project_bonus_override=entry.project_bonus_override,
        one_off_bonus=entry.one_off_bonus,
        contract_tds_rate=_setting(db, "contract_tds_rate", DEFAULT_CONTRACT_TDS_RATE),
        actual_advance_recovery=_recovered(db, entry.id),
    )
    return result, term, inputs


def save_snapshot(
    db: Session, entry: PayrollEntry, actor: User, kind: str
) -> PayrollSnapshot:
    result, term, inputs = preview_entry(db, entry)
    view = calculation_view(result)
    sequence = db.scalar(
        select(func.coalesce(func.max(PayrollSnapshot.sequence), 0)).where(
            PayrollSnapshot.payroll_entry_id == entry.id
        )
    )
    snapshot = PayrollSnapshot(
        payroll_entry_id=entry.id,
        sequence=int(sequence) + 1,
        kind=kind,
        pay_term_id=term.id,
        inputs_json=json.dumps(inputs, sort_keys=True),
        results_json=json.dumps(view, sort_keys=True),
        gross_payable=result.gross_payable,
        tds=result.tds,
        amount_paid=result.amount_paid,
        created_by_user_id=actor.id,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def latest_snapshot(db: Session, entry_id: int, kind: str) -> PayrollSnapshot | None:
    return db.scalars(
        select(PayrollSnapshot)
        .where(PayrollSnapshot.payroll_entry_id == entry_id, PayrollSnapshot.kind == kind)
        .order_by(PayrollSnapshot.sequence.desc())
    ).first()


def calculation_view(result: PayrollCalculation) -> dict:
    return {
        "member_type": result.member_type,
        "pay_basis": result.pay_basis,
        "payment_line_label": result.payment_line_label,
        "daily_rate": _exact(result.daily_rate),
        "hourly_equivalent": _exact(result.hourly_equivalent),
        "final_leaves": _exact(result.final_leaves),
        "final_extra_days": _exact(result.final_extra_days),
        "final_payment": _money(result.final_payment),
        "regular_pay": _money(result.regular_pay),
        "overtime_pay": _money(result.overtime_pay),
        "project_allowance": _money(result.project_allowance),
        "project_bonus": _money(result.project_bonus),
        "one_off_bonus": _money(result.one_off_bonus),
        "gross_payable": _money(result.gross_payable),
        "tds": _money(result.tds),
        "amount_paid": _money(result.amount_paid),
        "actual_advance_recovery": _money(result.actual_advance_recovery),
        "recovery_shortfall": _money(result.recovery_shortfall),
        "recovery_shortfall_flag": result.recovery_shortfall_flag,
        "net_deposit": _money(result.net_deposit),
    }


def _member_type(db: Session, entry: PayrollEntry) -> str:
    from app.db.models import Member

    member = db.get(Member, entry.member_id)
    if member is None:
        raise ServiceError("Member not found", 404)
    return member.member_type


def _recovered(db: Session, entry_id: int) -> Decimal:
    rows = db.scalars(
        select(AdvanceRecovery).where(AdvanceRecovery.payroll_entry_id == entry_id)
    ).all()
    return sum((row.recovered_amount for row in rows), Decimal("0"))


def _setting(db: Session, key: str, default: Decimal) -> Decimal:
    from app.services.company_settings import get_setting

    row = get_setting(db, key)
    if row is None or not row.value.strip():
        return default
    return Decimal(row.value)


def _plain(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _exact(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(MONEY), "f")
