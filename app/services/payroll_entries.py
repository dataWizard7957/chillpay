"""Draft monthly payroll entries.

Eligible-day counting is not decided. A member who did not work the whole
calendar month cannot have an entry yet. Proration is not applied.
Hourly entries store zero in the day columns because those columns are
required; payroll calculation must ignore them for hourly pay.
"""

import calendar
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Member, PayTerm, PayrollEntry, User, utcnow
from app.services.audit import record_audit
from app.services.errors import ServiceError
from app.services.pay_terms import list_pay_terms
from app.services.payroll_validation import PayrollValidationError, validate_payroll_inputs

PARTIAL_MONTH = (
    "Eligible-day counting is not decided, so a partial month cannot be saved."
)
PAY_TERM_CHANGES = (
    "Pay terms change during this month. That case is not decided, so the entry cannot be saved."
)


class _Unset:
    pass


UNSET = _Unset()


def list_entries(
    db: Session, *, member_id: int | None = None, year: int | None = None, month: int | None = None
) -> list[PayrollEntry]:
    query = select(PayrollEntry).order_by(
        PayrollEntry.year.desc(), PayrollEntry.month.desc(), PayrollEntry.id
    )
    if member_id is not None:
        query = query.where(PayrollEntry.member_id == member_id)
    if year is not None:
        query = query.where(PayrollEntry.year == year)
    if month is not None:
        query = query.where(PayrollEntry.month == month)
    return list(db.scalars(query))


def get_entry(db: Session, entry_id: int) -> PayrollEntry:
    entry = db.get(PayrollEntry, entry_id)
    if entry is None:
        raise ServiceError("Payroll entry not found", 404)
    return entry


def create_entry(db: Session, actor: User, fields: dict) -> PayrollEntry:
    member = _member(db, fields["member_id"])
    year, month = _period(fields["year"], fields["month"])
    _require_full_month(member, year, month)
    term = _term_for_full_month(db, member.id, year, month)
    values = _validated_inputs(term, fields, actor)
    entry = PayrollEntry(
        member_id=member.id,
        year=year,
        month=month,
        status="draft",
        comments=_clean_comments(fields.get("comments", UNSET)),
        created_by_user_id=actor.id,
        **values,
    )
    db.add(entry)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ServiceError("A payroll entry already exists for that member and month", 409) from exc
    record_audit(
        db,
        actor_user_id=actor.id,
        action="payroll.created",
        target_type="payroll_entry",
        target_id=str(entry.id),
        metadata={"member_id": member.id, "year": year, "month": month, "status": "draft"},
    )
    return entry


def update_entry(db: Session, entry: PayrollEntry, actor: User, fields: dict) -> PayrollEntry:
    if entry.status != "draft":
        raise ServiceError("Only draft payroll entries can be edited", 409)
    if fields.get("lock_version") != entry.lock_version:
        raise ServiceError("The payroll entry was changed by someone else", 409)
    member = _member(db, entry.member_id)
    _require_full_month(member, entry.year, entry.month)
    term = _term_for_full_month(db, member.id, entry.year, entry.month)
    values = _validated_inputs(term, fields, actor, existing=entry)
    for name, value in values.items():
        setattr(entry, name, value)
    if "comments" in fields:
        entry.comments = _clean_comments(fields["comments"])
    entry.updated_at = utcnow()
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ServiceError("The payroll entry could not be saved", 409) from exc
    record_audit(
        db,
        actor_user_id=actor.id,
        action="payroll.updated",
        target_type="payroll_entry",
        target_id=str(entry.id),
        metadata={
            "member_id": entry.member_id,
            "year": entry.year,
            "month": entry.month,
            "status": entry.status,
            "fields": sorted(key for key in fields if key != "lock_version"),
        },
    )
    return entry


def entry_view(entry: PayrollEntry, pay_basis: str | None) -> dict:
    return {
        "id": entry.id,
        "member_id": entry.member_id,
        "year": entry.year,
        "month": entry.month,
        "status": entry.status,
        "pay_basis": pay_basis,
        "total_leaves": _quantity(entry.total_leaves, Decimal("0.1")),
        "extra_work_days": _quantity(entry.extra_work_days, Decimal("0.1")),
        "comp_off": _quantity(entry.comp_off, Decimal("0.1")),
        "hours_worked": _quantity(entry.hours_worked, Decimal("0.01")),
        "overtime_hours": _quantity(entry.overtime_hours, Decimal("0.01")),
        "project_allowance_override": _quantity(entry.project_allowance_override, Decimal("0.01")),
        "project_bonus_override": _quantity(entry.project_bonus_override, Decimal("0.01")),
        "one_off_bonus": _quantity(entry.one_off_bonus, Decimal("0.01")),
        "one_off_bonus_authorized_by_user_id": entry.one_off_bonus_authorized_by_user_id,
        "comments": entry.comments,
        "lock_version": entry.lock_version,
    }


def basis_for_entry(db: Session, entry: PayrollEntry) -> str | None:
    try:
        return _term_for_full_month(db, entry.member_id, entry.year, entry.month).pay_basis
    except ServiceError:
        return None


def _validated_inputs(
    term: PayTerm, fields: dict, actor: User, existing: PayrollEntry | None = None
) -> dict:
    allowance = _override_value(
        actor,
        fields,
        existing,
        "project_allowance_override",
        "Only HR can override project allowance",
    )
    bonus = _override_value(
        actor, fields, existing, "project_bonus_override", "Only HR can override project bonus"
    )
    if "one_off_bonus" in fields and fields["one_off_bonus"] is not None:
        one_off_amount = fields["one_off_bonus"]
        authorizer = None
    elif existing is not None and "one_off_bonus" not in fields:
        one_off_amount = existing.one_off_bonus
        authorizer = existing.one_off_bonus_authorized_by_user_id
    else:
        one_off_amount = Decimal("0")
        authorizer = None
    if one_off_amount < 0:
        raise ServiceError("One-off bonus cannot be negative")
    if one_off_amount > 0 and "one_off_bonus" in fields:
        if actor.role != "hr":
            raise ServiceError("Only HR can authorize a one-off bonus")
        authorizer = actor.id
    try:
        validated = validate_payroll_inputs(
            pay_basis=term.pay_basis,
            monthly_payment=term.monthly_payment,
            hourly_rate=term.hourly_rate,
            total_leaves=_carried(fields, existing, "total_leaves", term.pay_basis),
            extra_work_days=_carried(fields, existing, "extra_work_days", term.pay_basis),
            comp_off=_carried(fields, existing, "comp_off", term.pay_basis),
            hours_worked=_carried(fields, existing, "hours_worked", term.pay_basis),
            overtime_hours=_carried(fields, existing, "overtime_hours", term.pay_basis),
        )
    except PayrollValidationError as exc:
        raise ServiceError(str(exc)) from exc
    for amount, label in ((allowance, "Project allowance"), (bonus, "Project bonus")):
        if isinstance(amount, Decimal) and amount < 0:
            raise ServiceError(f"{label} cannot be negative")
    return {
        "total_leaves": validated["total_leaves"] if validated["total_leaves"] is not None else Decimal("0"),
        "extra_work_days": validated["extra_work_days"]
        if validated["extra_work_days"] is not None
        else Decimal("0"),
        "comp_off": validated["comp_off"] if validated["comp_off"] is not None else Decimal("0"),
        "hours_worked": validated["hours_worked"],
        "overtime_hours": validated["overtime_hours"],
        "project_allowance_override": None if allowance is UNSET else allowance,
        "project_bonus_override": None if bonus is UNSET else bonus,
        "one_off_bonus": one_off_amount,
        "one_off_bonus_authorized_by_user_id": authorizer,
    }


def _carried(fields: dict, existing: PayrollEntry | None, name: str, pay_basis: str):
    if name in fields:
        return fields[name]
    if existing is None:
        return None
    if name in {"total_leaves", "extra_work_days", "comp_off"} and pay_basis == "hourly":
        return None
    return getattr(existing, name)


def _override_value(actor: User, fields: dict, existing: PayrollEntry | None, name: str, message: str):
    if name in fields:
        if actor.role != "hr":
            raise ServiceError(message)
        return fields[name]
    if existing is not None:
        return getattr(existing, name)
    return UNSET


def _member(db: Session, member_id: int) -> Member:
    member = db.get(Member, member_id)
    if member is None:
        raise ServiceError("Member not found", 404)
    return member


def _period(year: int, month: int) -> tuple[int, int]:
    if year < 1 or month < 1 or month > 12:
        raise ServiceError("Payroll month must be a real calendar month")
    return year, month


def _require_full_month(member: Member, year: int, month: int) -> None:
    start, end = _month_bounds(year, month)
    if member.joining_date > end or (
        member.last_working_date is not None and member.last_working_date < start
    ):
        raise ServiceError("The member was not employed for this month")
    if member.joining_date > start or (
        member.last_working_date is not None and member.last_working_date < end
    ):
        raise ServiceError(PARTIAL_MONTH)


def _term_for_full_month(db: Session, member_id: int, year: int, month: int) -> PayTerm:
    start, end = _month_bounds(year, month)
    covering = []
    overlapping = []
    for term in list_pay_terms(db, member_id):
        term_end = term.effective_to
        if term.effective_from <= end and (term_end is None or term_end >= start):
            overlapping.append(term)
        if term.effective_from <= start and (term_end is None or term_end >= end):
            covering.append(term)
    if len(covering) == 1:
        return covering[0]
    if overlapping:
        raise ServiceError(PAY_TERM_CHANGES)
    raise ServiceError("No pay term covers this month")


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _clean_comments(value) -> str | None:
    if value is UNSET or value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _quantity(value: Decimal | None, unit: Decimal) -> str | None:
    if value is None:
        return None
    return format(value.quantize(unit), "f")
