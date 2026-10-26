"""Backend authorization rules. These are the source of truth, not the UI."""

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Department,
    DepartmentHeadAssignment,
    DepartmentHeadLeave,
    Member,
    PayrollEntry,
    Project,
    ProjectAssignment,
    User,
    utcnow,
)


def today() -> date:
    return utcnow().date()


def current_head_assignment(
    db: Session, department_id: int, on_date: date | None = None
) -> DepartmentHeadAssignment | None:
    on_date = on_date or today()
    return db.scalars(
        select(DepartmentHeadAssignment).where(
            DepartmentHeadAssignment.department_id == department_id,
            DepartmentHeadAssignment.start_date <= on_date,
            or_(
                DepartmentHeadAssignment.end_date.is_(None),
                DepartmentHeadAssignment.end_date >= on_date,
            ),
        )
    ).first()


def is_explicitly_on_leave(
    db: Session, department_id: int, member_id: int, on_date: date | None = None
) -> bool:
    """True only when an uncancelled HR leave record covers the date. Never inferred."""

    on_date = on_date or today()
    row = db.scalars(
        select(DepartmentHeadLeave).where(
            DepartmentHeadLeave.department_id == department_id,
            DepartmentHeadLeave.member_id == member_id,
            DepartmentHeadLeave.start_date <= on_date,
            DepartmentHeadLeave.end_date >= on_date,
            DepartmentHeadLeave.cancelled_at.is_(None),
        )
    ).first()
    return row is not None


def member_is_current_head(
    db: Session, member_id: int, department_id: int, on_date: date | None = None
) -> bool:
    assignment = current_head_assignment(db, department_id, on_date)
    return assignment is not None and assignment.member_id == member_id


def _entry_department_id(db: Session, entry: PayrollEntry) -> int:
    member = db.get(Member, entry.member_id)
    if member is None:
        raise ValueError("payroll entry member no longer exists")
    return member.department_id


def can_access_member_record(
    db: Session, user: User, member: Member, on_date: date | None = None
) -> bool:
    if user.role == "hr":
        return True
    if user.role == "finance":
        return False
    if user.role == "member":
        return user.member_id is not None and user.member_id == member.id
    if user.role == "department_head":
        return member_is_current_head(db, user.member_id or 0, member.department_id, on_date)
    return False


def can_write(user: User) -> bool:
    return user.role != "finance"


def can_manage_org(user: User) -> bool:
    return user.role == "hr"


def can_edit_payroll_draft(user: User, member: Member) -> bool:
    if user.role == "hr":
        return True
    return user.role == "member" and user.member_id is not None and user.member_id == member.id


def can_view_department(db: Session, user: User, department: Department, on_date: date | None = None) -> bool:
    if user.role in {"hr", "finance"}:
        return True
    if user.role == "department_head":
        return member_is_current_head(db, user.member_id or 0, department.id, on_date)
    if user.role == "member" and user.member_id is not None:
        member = db.get(Member, user.member_id)
        return member is not None and member.department_id == department.id
    return False


def can_view_project(db: Session, user: User, project: Project) -> bool:
    if user.role in {"hr", "finance", "department_head"}:
        return True
    if user.role == "member" and user.member_id is not None:
        assignment = db.scalars(
            select(ProjectAssignment).where(
                ProjectAssignment.member_id == user.member_id,
                ProjectAssignment.project_id == project.id,
            )
        ).first()
        return assignment is not None
    return False


def can_approve_payroll_entry(
    db: Session, user: User, entry: PayrollEntry, on_date: date | None = None
) -> bool:
    if user.role in {"member", "finance"}:
        return False

    department_id = _entry_department_id(db, entry)
    entry_is_head = member_is_current_head(db, entry.member_id, department_id, on_date)

    if user.role == "department_head":
        if user.member_id == entry.member_id or entry_is_head:
            return False
        return member_is_current_head(db, user.member_id or 0, department_id, on_date)

    if user.role == "hr":
        if entry_is_head:
            return True
        assignment = current_head_assignment(db, department_id, on_date)
        if assignment is None:
            return True
        return is_explicitly_on_leave(db, department_id, assignment.member_id, on_date)

    return False
