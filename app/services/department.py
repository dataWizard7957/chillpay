"""Department records and dated head assignments."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.policies import current_head_assignment
from app.db.models import Department, DepartmentHeadAssignment, Member, User, utcnow
from app.services.audit import record_audit
from app.services.errors import ServiceError

INITIAL_DEPARTMENTS = ("HR", "Admin", "Digitisation", "Development")


def list_departments(db: Session) -> list[Department]:
    return list(db.scalars(select(Department).order_by(Department.name, Department.id)))


def get_department(db: Session, department_id: int) -> Department:
    department = db.get(Department, department_id)
    if department is None:
        raise ServiceError("Department not found", 404)
    return department


def create_department(db: Session, *, name: str, is_active: bool, actor: User) -> Department:
    department = Department(name=_clean_name(name), is_active=is_active)
    db.add(department)
    _flush_unique(db, "A department with this name already exists")
    record_audit(
        db,
        actor_user_id=actor.id,
        action="department.created",
        target_type="department",
        target_id=str(department.id),
        metadata={"name": department.name, "is_active": department.is_active},
    )
    return department


def update_department(
    db: Session,
    department: Department,
    *,
    actor: User,
    name: str | None = None,
    is_active: bool | None = None,
) -> Department:
    if name is None and is_active is None:
        raise ServiceError("No department fields to update")
    if name is not None:
        department.name = _clean_name(name)
    if is_active is not None:
        department.is_active = is_active
    department.updated_at = utcnow()
    _flush_unique(db, "A department with this name already exists")
    record_audit(
        db,
        actor_user_id=actor.id,
        action="department.updated",
        target_type="department",
        target_id=str(department.id),
        metadata={"name": department.name, "is_active": department.is_active},
    )
    return department


def list_head_assignments(db: Session, department_id: int) -> list[DepartmentHeadAssignment]:
    return list(
        db.scalars(
            select(DepartmentHeadAssignment)
            .where(DepartmentHeadAssignment.department_id == department_id)
            .order_by(
                DepartmentHeadAssignment.start_date.desc(), DepartmentHeadAssignment.id.desc()
            )
        )
    )


def assign_department_head(
    db: Session,
    department: Department,
    *,
    member_id: int,
    start_date: date,
    actor: User,
) -> DepartmentHeadAssignment:
    if not department.is_active:
        raise ServiceError("Cannot assign a head to an inactive department")
    member = db.get(Member, member_id)
    if member is None:
        raise ServiceError("Member not found")
    if not member.is_active:
        raise ServiceError("Member is not active")

    current = db.scalars(
        select(DepartmentHeadAssignment).where(
            DepartmentHeadAssignment.department_id == department.id,
            DepartmentHeadAssignment.end_date.is_(None),
        )
    ).first()
    if current is not None:
        if current.member_id == member_id:
            raise ServiceError("That member is already the current department head", 409)
        if start_date <= current.start_date:
            raise ServiceError(
                "The replacement start date must be after the current assignment start date"
            )
        current.end_date = start_date - timedelta(days=1)
        record_audit(
            db,
            actor_user_id=actor.id,
            action="department_head.ended",
            target_type="department_head_assignment",
            target_id=str(current.id),
            metadata={
                "department_id": department.id,
                "member_id": current.member_id,
                "end_date": current.end_date.isoformat(),
            },
        )

    assignment = DepartmentHeadAssignment(
        department_id=department.id,
        member_id=member.id,
        start_date=start_date,
        created_by_user_id=actor.id,
    )
    db.add(assignment)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ServiceError("A department can have only one current head", 409) from exc
    record_audit(
        db,
        actor_user_id=actor.id,
        action="department_head.assigned",
        target_type="department_head_assignment",
        target_id=str(assignment.id),
        metadata={
            "department_id": department.id,
            "member_id": member.id,
            "start_date": start_date.isoformat(),
        },
    )
    return assignment


def close_current_head(
    db: Session, department: Department, *, end_date: date, actor: User
) -> DepartmentHeadAssignment:
    current = current_head_assignment(db, department.id, end_date)
    if current is None or current.end_date is not None:
        raise ServiceError("There is no current department head to close", 404)
    if end_date < current.start_date:
        raise ServiceError("End date cannot be before the assignment start date")
    current.end_date = end_date
    record_audit(
        db,
        actor_user_id=actor.id,
        action="department_head.ended",
        target_type="department_head_assignment",
        target_id=str(current.id),
        metadata={
            "department_id": department.id,
            "member_id": current.member_id,
            "end_date": end_date.isoformat(),
        },
    )
    return current


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ServiceError("Name is required")
    if len(cleaned) > 100:
        raise ServiceError("Name is too long")
    return cleaned


def _flush_unique(db: Session, message: str) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ServiceError(message, 409) from exc
