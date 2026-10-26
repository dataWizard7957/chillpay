"""Payroll drafts, calculation previews, and the approval workflow."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db, require_csrf, require_user
from app.auth.policies import can_access_member_record, can_edit_payroll_draft
from app.db.models import User
from app.services.errors import ServiceError
from app.services.members import get_member
from app.services.pay_terms import parse_money
from app.services.payroll_entries import (
    UNSET,
    basis_for_entry,
    create_entry,
    entry_view,
    get_entry,
    list_entries,
    update_entry,
)
from app.api.advances import RecoveryBody, record_entry_recovery
from app.api.invoices import generate_entry_invoice
from app.services.payroll_preview import calculation_view, preview_entry
from app.services.payroll_workflow import approve_entry, reject_entry, reopen_entry, submit_entry

router = APIRouter(prefix="/payroll-entries", tags=["payroll"])

_DAY_FIELDS = ("total_leaves", "extra_work_days", "comp_off")
_HOUR_FIELDS = ("hours_worked", "overtime_hours")
_MONEY_FIELDS = (
    "project_allowance_override",
    "project_bonus_override",
    "one_off_bonus",
)


class PayrollWrite(BaseModel):
    member_id: int
    year: int
    month: int
    total_leaves: str | None = None
    extra_work_days: str | None = None
    comp_off: str | None = None
    hours_worked: str | None = None
    overtime_hours: str | None = None
    project_allowance_override: str | None = None
    project_bonus_override: str | None = None
    one_off_bonus: str | None = None
    comments: str | None = None


class WorkflowBody(BaseModel):
    lock_version: int
    comment: str | None = None


class PayrollPatch(BaseModel):
    lock_version: int
    total_leaves: str | None = None
    extra_work_days: str | None = None
    comp_off: str | None = None
    hours_worked: str | None = None
    overtime_hours: str | None = None
    project_allowance_override: str | None = None
    project_bonus_override: str | None = None
    one_off_bonus: str | None = None
    comments: str | None = None


def _raise_service(exc: ServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _parsed(raw: dict) -> dict:
    parsed = dict(raw)
    for name in _DAY_FIELDS + _HOUR_FIELDS + _MONEY_FIELDS:
        if name in parsed:
            parsed[name] = parse_money(parsed[name], name.replace("_", " "))
    return parsed


@router.get("")
def get_entries(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    member_id: int | None = None,
    year: int | None = Query(default=None),
    month: int | None = Query(default=None),
) -> dict:
    if user.role == "finance":
        raise HTTPException(status_code=403, detail="Not authorized")
    visible = []
    for entry in list_entries(db, member_id=member_id, year=year, month=month):
        member = get_member(db, entry.member_id)
        if can_access_member_record(db, user, member):
            visible.append(entry_view(entry, basis_for_entry(db, entry)))
    return {"payroll_entries": visible}


@router.get("/{entry_id}")
def get_one_entry(
    entry_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict:
    try:
        entry = get_entry(db, entry_id)
        member = get_member(db, entry.member_id)
    except ServiceError as exc:
        _raise_service(exc)
    if not can_access_member_record(db, user, member):
        raise HTTPException(status_code=403, detail="Not authorized")
    return {"payroll_entry": entry_view(entry, basis_for_entry(db, entry))}


@router.post("")
def post_entry(
    body: PayrollWrite,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        member = get_member(db, body.member_id)
    except ServiceError as exc:
        _raise_service(exc)
    if not can_edit_payroll_draft(user, member):
        raise HTTPException(status_code=403, detail="Not authorized")
    try:
        entry = create_entry(db, user, _parsed(body.model_dump(exclude_unset=True)))
    except ServiceError as exc:
        _raise_service(exc)
    return {"payroll_entry": entry_view(entry, basis_for_entry(db, entry))}


@router.patch("/{entry_id}")
def patch_entry(
    entry_id: int,
    body: PayrollPatch,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        entry = get_entry(db, entry_id)
        member = get_member(db, entry.member_id)
    except ServiceError as exc:
        _raise_service(exc)
    if not can_edit_payroll_draft(user, member):
        raise HTTPException(status_code=403, detail="Not authorized")
    changes = _parsed(body.model_dump(exclude_unset=True))
    changes.pop("lock_version", None)
    changes["lock_version"] = body.lock_version
    if "comments" not in body.model_fields_set:
        changes.pop("comments", None)
    try:
        updated = update_entry(db, entry, user, changes)
    except ServiceError as exc:
        _raise_service(exc)
    return {"payroll_entry": entry_view(updated, basis_for_entry(db, updated))}


def _authorized_entry(db: Session, user: User, entry_id: int):
    try:
        entry = get_entry(db, entry_id)
        member = get_member(db, entry.member_id)
    except ServiceError as exc:
        _raise_service(exc)
    if not can_access_member_record(db, user, member):
        raise HTTPException(status_code=403, detail="Not authorized")
    return entry


@router.get("/{entry_id}/preview")
def get_preview(
    entry_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict:
    entry = _authorized_entry(db, user, entry_id)
    try:
        result, _term, _inputs = preview_entry(db, entry)
    except ServiceError as exc:
        _raise_service(exc)
    return {"calculation": calculation_view(result)}


@router.post("/{entry_id}/submit")
def post_submit(
    entry_id: int,
    body: WorkflowBody,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    entry = _authorized_entry(db, user, entry_id)
    try:
        updated = submit_entry(db, entry, user, body.lock_version)
    except ServiceError as exc:
        _raise_service(exc)
    return {"payroll_entry": entry_view(updated, basis_for_entry(db, updated))}


@router.post("/{entry_id}/approve")
def post_approve(
    entry_id: int,
    body: WorkflowBody,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    entry = _authorized_entry(db, user, entry_id)
    try:
        updated = approve_entry(db, entry, user, body.lock_version)
    except ServiceError as exc:
        _raise_service(exc)
    return {"payroll_entry": entry_view(updated, basis_for_entry(db, updated))}


@router.post("/{entry_id}/reject")
def post_reject(
    entry_id: int,
    body: WorkflowBody,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    entry = _authorized_entry(db, user, entry_id)
    try:
        updated = reject_entry(db, entry, user, body.lock_version, body.comment)
    except ServiceError as exc:
        _raise_service(exc)
    return {"payroll_entry": entry_view(updated, basis_for_entry(db, updated))}


@router.post("/{entry_id}/reopen")
def post_reopen(
    entry_id: int,
    body: WorkflowBody,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    entry = _authorized_entry(db, user, entry_id)
    try:
        updated = reopen_entry(db, entry, user, body.lock_version, body.comment)
    except ServiceError as exc:
        _raise_service(exc)
    return {"payroll_entry": entry_view(updated, basis_for_entry(db, updated))}


@router.post("/{entry_id}/recoveries")
def post_recovery(
    entry_id: int,
    body: RecoveryBody,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    entry = _authorized_entry(db, user, entry_id)
    return record_entry_recovery(db, user, entry, body)


@router.post("/{entry_id}/invoice")
def post_invoice(
    entry_id: int,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    _authorized_entry(db, user, entry_id)
    return generate_entry_invoice(db, user, entry_id)
