"""Explicit department-head leave. Leave is never inferred from absence."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.policies import member_is_current_head
from app.db.models import Department, DepartmentHeadLeave, User, utcnow
from app.services.audit import record_audit
from app.services.errors import ServiceError


def record_head_leave(
    db: Session,
    department: Department,
    *,
    actor: User,
    member_id: int,
    start_date: date,
    end_date: date,
    reason: str | None,
) -> DepartmentHeadLeave:
    if end_date < start_date:
        raise ServiceError("Leave end date cannot be before the start date")
    if not member_is_current_head(db, member_id, department.id, start_date):
        raise ServiceError("That member is not the department head")
    cleaned = None if reason is None else reason.strip() or None
    row = DepartmentHeadLeave(
        department_id=department.id,
        member_id=member_id,
        start_date=start_date,
        end_date=end_date,
        reason=cleaned,
        created_by_user_id=actor.id,
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        actor_user_id=actor.id,
        action="department_head.leave_recorded",
        target_type="department_head_leave",
        target_id=str(row.id),
        metadata={
            "department_id": department.id,
            "member_id": member_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    )
    return row


def cancel_head_leave(db: Session, leave: DepartmentHeadLeave, actor: User) -> DepartmentHeadLeave:
    if leave.cancelled_at is not None:
        raise ServiceError("This leave record is already cancelled", 409)
    leave.cancelled_at = utcnow()
    leave.cancelled_by_user_id = actor.id
    db.flush()
    record_audit(
        db,
        actor_user_id=actor.id,
        action="department_head.leave_cancelled",
        target_type="department_head_leave",
        target_id=str(leave.id),
        metadata={"department_id": leave.department_id, "member_id": leave.member_id},
    )
    return leave


def list_head_leave(db: Session, department_id: int) -> list[DepartmentHeadLeave]:
    return list(
        db.scalars(
            select(DepartmentHeadLeave)
            .where(DepartmentHeadLeave.department_id == department_id)
            .order_by(DepartmentHeadLeave.start_date, DepartmentHeadLeave.id)
        ).all()
    )


def get_head_leave(db: Session, leave_id: int) -> DepartmentHeadLeave:
    row = db.get(DepartmentHeadLeave, leave_id)
    if row is None:
        raise ServiceError("Leave record not found", 404)
    return row
