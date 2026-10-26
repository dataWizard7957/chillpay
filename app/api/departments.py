"""Department records and head-assignment history."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db, require_csrf, require_roles, require_user
from app.auth.policies import can_manage_org, can_view_department, current_head_assignment
from app.db.models import Department, DepartmentHeadAssignment, User
from app.services.head_leave import cancel_head_leave, get_head_leave, list_head_leave, record_head_leave
from app.services.department import (
    assign_department_head,
    close_current_head,
    create_department,
    get_department,
    list_departments,
    list_head_assignments,
    update_department,
)
from app.services.errors import ServiceError

router = APIRouter(prefix="/departments", tags=["departments"])


class DepartmentWrite(BaseModel):
    name: str
    is_active: bool = True


class DepartmentPatch(BaseModel):
    name: str | None = None
    is_active: bool | None = None


class HeadAssign(BaseModel):
    member_id: int
    start_date: date


class HeadClose(BaseModel):
    end_date: date


class LeaveWrite(BaseModel):
    member_id: int
    start_date: date
    end_date: date
    reason: str | None = None


def _raise_service(exc: ServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _department_payload(db: Session, department: Department) -> dict:
    head = current_head_assignment(db, department.id)
    return {
        "id": department.id,
        "name": department.name,
        "is_active": department.is_active,
        "current_head": None
        if head is None
        else {
            "assignment_id": head.id,
            "member_id": head.member_id,
            "start_date": head.start_date.isoformat(),
        },
        "created_at": department.created_at.isoformat(sep=" "),
        "updated_at": department.updated_at.isoformat(sep=" "),
    }


def _assignment_payload(row: DepartmentHeadAssignment) -> dict:
    return {
        "id": row.id,
        "department_id": row.department_id,
        "member_id": row.member_id,
        "start_date": row.start_date.isoformat(),
        "end_date": None if row.end_date is None else row.end_date.isoformat(),
    }


def _visible_department(db: Session, user: User, department_id: int) -> Department:
    try:
        department = get_department(db, department_id)
    except ServiceError as exc:
        _raise_service(exc)
    if not can_view_department(db, user, department):
        raise HTTPException(status_code=403, detail="Not authorized")
    return department


@router.get("")
def get_departments(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    visible = [
        department
        for department in list_departments(db)
        if can_view_department(db, user, department)
    ]
    return {"departments": [_department_payload(db, department) for department in visible]}


@router.get("/{department_id}")
def get_one_department(
    department_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict:
    department = _visible_department(db, user, department_id)
    return {"department": _department_payload(db, department)}


@router.get("/{department_id}/heads")
def get_department_heads(
    department_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict:
    department = _visible_department(db, user, department_id)
    return {
        "assignments": [_assignment_payload(row) for row in list_head_assignments(db, department.id)]
    }


@router.post("")
def post_department(
    body: DepartmentWrite,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if not can_manage_org(user):
        raise HTTPException(status_code=403, detail="Not authorized")
    try:
        department = create_department(db, name=body.name, is_active=body.is_active, actor=user)
    except ServiceError as exc:
        _raise_service(exc)
    return {"department": _department_payload(db, department)}


@router.patch("/{department_id}")
def patch_department(
    department_id: int,
    body: DepartmentPatch,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        department = update_department(
            db,
            get_department(db, department_id),
            actor=user,
            name=body.name,
            is_active=body.is_active,
        )
    except ServiceError as exc:
        _raise_service(exc)
    return {"department": _department_payload(db, department)}


@router.post("/{department_id}/heads")
def post_department_head(
    department_id: int,
    body: HeadAssign,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        assignment = assign_department_head(
            db,
            get_department(db, department_id),
            member_id=body.member_id,
            start_date=body.start_date,
            actor=user,
        )
    except ServiceError as exc:
        _raise_service(exc)
    return {"assignment": _assignment_payload(assignment)}


@router.post("/{department_id}/heads/close")
def post_close_department_head(
    department_id: int,
    body: HeadClose,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        assignment = close_current_head(
            db, get_department(db, department_id), end_date=body.end_date, actor=user
        )
    except ServiceError as exc:
        _raise_service(exc)
    return {"assignment": _assignment_payload(assignment)}


def _leave_payload(row, *, include_reason: bool) -> dict:
    return {
        "id": row.id,
        "department_id": row.department_id,
        "member_id": row.member_id,
        "start_date": row.start_date.isoformat(),
        "end_date": row.end_date.isoformat(),
        "reason": row.reason if include_reason else None,
        "cancelled_at": None if row.cancelled_at is None else row.cancelled_at.isoformat(sep=" "),
    }


@router.get("/{department_id}/leaves")
def get_leaves(
    department_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict:
    department = _visible_department(db, user, department_id)
    return {
        "leaves": [
            _leave_payload(row, include_reason=user.role == "hr")
            for row in list_head_leave(db, department.id)
        ]
    }


@router.post("/{department_id}/leaves")
def post_leave(
    department_id: int,
    body: LeaveWrite,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = record_head_leave(
            db,
            get_department(db, department_id),
            actor=user,
            member_id=body.member_id,
            start_date=body.start_date,
            end_date=body.end_date,
            reason=body.reason,
        )
    except ServiceError as exc:
        _raise_service(exc)
    return {"leave": _leave_payload(row, include_reason=True)}


@router.post("/{department_id}/leaves/{leave_id}/cancel")
def post_cancel_leave(
    department_id: int,
    leave_id: int,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = get_head_leave(db, leave_id)
        if row.department_id != department_id:
            raise ServiceError("Leave record not found", 404)
        cancelled = cancel_head_leave(db, row, user)
    except ServiceError as exc:
        _raise_service(exc)
    return {"leave": _leave_payload(cancelled, include_reason=True)}
