"""Advance schedules and actual recovery. Recovery starts the month after payment."""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Advance, AdvanceInstallment, AdvanceRecovery, Member, PayrollEntry, User
from app.services.audit import record_audit
from app.services.errors import ServiceError
from app.services.payroll_preview import preview_entry

MONEY = Decimal("0.01")


def split_installments(amount: Decimal, months: int) -> list[Decimal]:
    """Split an amount into monthly parts. The final part makes the total exact."""

    if months < 1:
        raise ServiceError("Recovery duration must be at least one month")
    if amount <= 0:
        raise ServiceError("Advance amount must be greater than zero")
    base = (amount / Decimal(months)).quantize(MONEY, rounding=ROUND_HALF_UP)
    parts = [base for _ in range(months)]
    parts[-1] = (amount - sum(parts[:-1], Decimal("0"))).quantize(MONEY, rounding=ROUND_HALF_UP)
    if parts[-1] <= 0:
        raise ServiceError("The installment split could not be made exact")
    return parts


def create_advance(
    db: Session,
    *,
    actor: User,
    member_id: int,
    original_amount: Decimal,
    date_paid: date,
    recovery_months: int = 6,
    notes: str | None = None,
) -> Advance:
    member = db.get(Member, member_id)
    if member is None:
        raise ServiceError("Member not found", 404)
    advance = Advance(
        member_id=member.id,
        original_amount=original_amount,
        date_paid=date_paid,
        recovery_months=recovery_months,
        notes=_clean_notes(notes),
        created_by_user_id=actor.id,
        needs_hr_review=_left_with_balance(member),
    )
    db.add(advance)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ServiceError("The advance could not be saved", 409) from exc
    _write_schedule(db, advance, original_amount, recovery_months, *next_month(date_paid.year, date_paid.month), 1)
    record_audit(
        db,
        actor_user_id=actor.id,
        action="advance.created",
        target_type="advance",
        target_id=str(advance.id),
        metadata={"member_id": member.id, "recovery_months": recovery_months},
    )
    return advance


def reschedule_advance(
    db: Session,
    advance: Advance,
    *,
    actor: User,
    recovery_months: int,
    start_year: int,
    start_month: int,
) -> Advance:
    remaining = outstanding_amount(db, advance)
    if remaining <= 0:
        raise ServiceError("There is no outstanding balance to reschedule", 409)
    version = current_schedule_version(db, advance.id) + 1
    _write_schedule(db, advance, remaining, recovery_months, start_year, start_month, version)
    advance.recovery_months = recovery_months
    record_audit(
        db,
        actor_user_id=actor.id,
        action="advance.rescheduled",
        target_type="advance",
        target_id=str(advance.id),
        metadata={"schedule_version": version, "recovery_months": recovery_months},
    )
    return advance


def record_recovery(
    db: Session,
    entry: PayrollEntry,
    advance: Advance,
    *,
    actor: User,
    amount: Decimal,
) -> AdvanceRecovery:
    if entry.status not in {"draft", "submitted"}:
        raise ServiceError("Recovery must be recorded before the entry is approved", 409)
    if advance.member_id != entry.member_id:
        raise ServiceError("The advance belongs to a different member")
    if amount < 0:
        raise ServiceError("Recovery amount cannot be negative")
    remaining = outstanding_amount(db, advance)
    result, _term, _inputs = preview_entry(db, entry)
    room = result.amount_paid - result.actual_advance_recovery
    if room < 0:
        room = Decimal("0.00")
    recovered = min(amount, remaining, room).quantize(MONEY, rounding=ROUND_HALF_UP)
    scheduled = scheduled_amount_for_month(db, advance, entry.year, entry.month)
    shortfall = (scheduled - recovered).quantize(MONEY, rounding=ROUND_HALF_UP)
    if shortfall < 0:
        shortfall = Decimal("0.00")
    if amount > recovered:
        shortfall = max(shortfall, (amount - recovered).quantize(MONEY, rounding=ROUND_HALF_UP))
    row = AdvanceRecovery(
        advance_id=advance.id,
        payroll_entry_id=entry.id,
        scheduled_amount=scheduled,
        recovered_amount=recovered,
        shortfall_amount=shortfall,
        recorded_by_user_id=actor.id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        message = str(exc.orig).casefold()
        if "exceeds the original" in message:
            raise ServiceError("Recovery would exceed the original advance", 409) from exc
        raise ServiceError("A recovery already exists for this advance and payroll entry", 409) from exc
    if outstanding_amount(db, advance) <= 0:
        advance.status = "closed"
    member = db.get(Member, advance.member_id)
    if shortfall > 0 or (member is not None and _left_with_balance(member) and outstanding_amount(db, advance) > 0):
        advance.needs_hr_review = True
    record_audit(
        db,
        actor_user_id=actor.id,
        action="advance.recovery_recorded",
        target_type="advance",
        target_id=str(advance.id),
        metadata={
            "payroll_entry_id": entry.id,
            "shortfall_flag": shortfall > 0,
        },
    )
    return row


def flag_outstanding_advances(db: Session, member: Member) -> None:
    if member.last_working_date is None:
        return
    rows = db.scalars(select(Advance).where(Advance.member_id == member.id, Advance.status == "active")).all()
    for advance in rows:
        if outstanding_amount(db, advance) > 0:
            advance.needs_hr_review = True


def outstanding_amount(db: Session, advance: Advance) -> Decimal:
    recovered = sum(
        (
            row.recovered_amount
            for row in db.scalars(
                select(AdvanceRecovery).where(AdvanceRecovery.advance_id == advance.id)
            ).all()
        ),
        Decimal("0"),
    )
    return (advance.original_amount - recovered).quantize(MONEY, rounding=ROUND_HALF_UP)


def current_schedule_version(db: Session, advance_id: int) -> int:
    version = db.scalar(
        select(func.coalesce(func.max(AdvanceInstallment.schedule_version), 0)).where(
            AdvanceInstallment.advance_id == advance_id
        )
    )
    return int(version or 0)


def scheduled_amount_for_month(db: Session, advance: Advance, year: int, month: int) -> Decimal:
    version = current_schedule_version(db, advance.id)
    row = db.scalars(
        select(AdvanceInstallment).where(
            AdvanceInstallment.advance_id == advance.id,
            AdvanceInstallment.schedule_version == version,
            AdvanceInstallment.due_year == year,
            AdvanceInstallment.due_month == month,
        )
    ).first()
    if row is None:
        return Decimal("0.00")
    return row.amount


def advance_view(db: Session, advance: Advance, *, include_notes: bool) -> dict:
    version = current_schedule_version(db, advance.id)
    installments = db.scalars(
        select(AdvanceInstallment)
        .where(
            AdvanceInstallment.advance_id == advance.id,
            AdvanceInstallment.schedule_version == version,
        )
        .order_by(AdvanceInstallment.sequence)
    ).all()
    recovered = (advance.original_amount - outstanding_amount(db, advance)).quantize(MONEY)
    return {
        "id": advance.id,
        "member_id": advance.member_id,
        "original_amount": format(advance.original_amount, "f"),
        "date_paid": advance.date_paid.isoformat(),
        "recovery_months": advance.recovery_months,
        "status": advance.status,
        "needs_hr_review": advance.needs_hr_review,
        "recovered_to_date": format(recovered, "f"),
        "outstanding": format(outstanding_amount(db, advance), "f"),
        "notes": advance.notes if include_notes else None,
        "installments": [
            {
                "sequence": row.sequence,
                "due_year": row.due_year,
                "due_month": row.due_month,
                "amount": format(row.amount, "f"),
            }
            for row in installments
        ],
    }


def next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _write_schedule(
    db: Session,
    advance: Advance,
    amount: Decimal,
    months: int,
    start_year: int,
    start_month: int,
    version: int,
) -> None:
    if start_month < 1 or start_month > 12 or start_year < 1:
        raise ServiceError("The schedule start month is invalid")
    year, month = start_year, start_month
    for sequence, part in enumerate(split_installments(amount, months), start=1):
        db.add(
            AdvanceInstallment(
                advance_id=advance.id,
                schedule_version=version,
                sequence=sequence,
                due_year=year,
                due_month=month,
                amount=part,
            )
        )
        year, month = next_month(year, month)
    db.flush()


def _left_with_balance(member: Member) -> bool:
    return member.last_working_date is not None


def _clean_notes(notes: str | None) -> str | None:
    if notes is None:
        return None
    cleaned = notes.strip()
    return cleaned or None
