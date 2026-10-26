"""Advance ledger. Finance can read it. Only HR can change it."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db, require_csrf, require_user
from app.auth.policies import can_access_member_record
from app.db.models import Advance, PayrollEntry, User
from app.services.advances import (
    advance_view,
    create_advance,
    record_recovery,
    reschedule_advance,
)
from app.services.errors import ServiceError
from app.services.members import get_member
from app.services.pay_terms import parse_money
from app.services.payroll_entries import get_entry

router = APIRouter(prefix="/advances", tags=["advances"])


class AdvanceWrite(BaseModel):
    member_id: int
    original_amount: str
    date_paid: date
    recovery_months: int = 6
    notes: str | None = None


class RescheduleBody(BaseModel):
    recovery_months: int
    start_year: int
    start_month: int


class RecoveryBody(BaseModel):
    advance_id: int
    amount: str


def _raise_service(exc: ServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _can_read(db: Session, user: User, member_id: int) -> bool:
    if user.role == "finance":
        return True
    try:
        member = get_member(db, member_id)
    except ServiceError as exc:
        _raise_service(exc)
    return can_access_member_record(db, user, member)


@router.get("")
def get_advances(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Advance).order_by(Advance.id)).all()
    visible = [row for row in rows if _can_read(db, user, row.member_id)]
    return {
        "advances": [
            advance_view(db, row, include_notes=user.role == "hr") for row in visible
        ]
    }


@router.get("/{advance_id}")
def get_one_advance(
    advance_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict:
    advance = db.get(Advance, advance_id)
    if advance is None:
        raise HTTPException(status_code=404, detail="Advance not found")
    if not _can_read(db, user, advance.member_id):
        raise HTTPException(status_code=403, detail="Not authorized")
    return {"advance": advance_view(db, advance, include_notes=user.role == "hr")}


@router.post("")
def post_advance(
    body: AdvanceWrite,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if user.role != "hr":
        raise HTTPException(status_code=403, detail="Not authorized")
    amount = parse_money(body.original_amount, "original amount")
    if amount is None or amount <= 0:
        raise HTTPException(status_code=400, detail="Advance amount must be greater than zero")
    try:
        advance = create_advance(
            db,
            actor=user,
            member_id=body.member_id,
            original_amount=amount,
            date_paid=body.date_paid,
            recovery_months=body.recovery_months,
            notes=body.notes,
        )
    except ServiceError as exc:
        _raise_service(exc)
    return {"advance": advance_view(db, advance, include_notes=True)}


@router.post("/{advance_id}/reschedule")
def post_reschedule(
    advance_id: int,
    body: RescheduleBody,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if user.role != "hr":
        raise HTTPException(status_code=403, detail="Not authorized")
    advance = db.get(Advance, advance_id)
    if advance is None:
        raise HTTPException(status_code=404, detail="Advance not found")
    try:
        updated = reschedule_advance(
            db,
            advance,
            actor=user,
            recovery_months=body.recovery_months,
            start_year=body.start_year,
            start_month=body.start_month,
        )
    except ServiceError as exc:
        _raise_service(exc)
    return {"advance": advance_view(db, updated, include_notes=True)}


def record_entry_recovery(
    db: Session, user: User, entry: PayrollEntry, body: RecoveryBody
) -> dict:
    if user.role != "hr":
        raise HTTPException(status_code=403, detail="Not authorized")
    advance = db.get(Advance, body.advance_id)
    if advance is None:
        raise HTTPException(status_code=404, detail="Advance not found")
    amount = parse_money(body.amount, "recovery amount")
    if amount is None:
        raise HTTPException(status_code=400, detail="Recovery amount is required")
    try:
        row = record_recovery(db, entry, advance, actor=user, amount=amount)
    except ServiceError as exc:
        _raise_service(exc)
    return {
        "recovery": {
            "id": row.id,
            "advance_id": row.advance_id,
            "payroll_entry_id": row.payroll_entry_id,
            "scheduled_amount": format(row.scheduled_amount, "f"),
            "recovered_amount": format(row.recovered_amount, "f"),
            "shortfall_amount": format(row.shortfall_amount, "f"),
        }
    }
