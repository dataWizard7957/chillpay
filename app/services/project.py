"""Projects and dated member assignments. A member has at most one open assignment."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import PROJECT_STATUSES, Member, Project, ProjectAssignment, User, utcnow
from app.services.audit import record_audit
from app.services.errors import ServiceError


def list_projects(db: Session) -> list[Project]:
    return list(db.scalars(select(Project).order_by(Project.name, Project.id)))


def get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise ServiceError("Project not found", 404)
    return project


def create_project(
    db: Session,
    *,
    name: str,
    description: str | None,
    status: str,
    actor: User,
) -> Project:
    project = Project(name=_clean_name(name), description=_clean_description(description), status=_clean_status(status))
    db.add(project)
    _flush_unique(db, "A project with this name already exists")
    record_audit(
        db,
        actor_user_id=actor.id,
        action="project.created",
        target_type="project",
        target_id=str(project.id),
        metadata={"name": project.name, "status": project.status},
    )
    return project


def update_project(
    db: Session,
    project: Project,
    *,
    actor: User,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
) -> Project:
    if name is None and description is None and status is None:
        raise ServiceError("No project fields to update")
    if name is not None:
        project.name = _clean_name(name)
    if description is not None:
        project.description = _clean_description(description)
    if status is not None:
        project.status = _clean_status(status)
    project.updated_at = utcnow()
    _flush_unique(db, "A project with this name already exists")
    record_audit(
        db,
        actor_user_id=actor.id,
        action="project.updated",
        target_type="project",
        target_id=str(project.id),
        metadata={"name": project.name, "status": project.status},
    )
    return project


def list_assignments(db: Session, project_id: int) -> list[ProjectAssignment]:
    return list(
        db.scalars(
            select(ProjectAssignment)
            .where(ProjectAssignment.project_id == project_id)
            .order_by(ProjectAssignment.start_date.desc(), ProjectAssignment.id.desc())
        )
    )


def current_open_assignment(db: Session, member_id: int) -> ProjectAssignment | None:
    return db.scalars(
        select(ProjectAssignment).where(
            ProjectAssignment.member_id == member_id,
            ProjectAssignment.end_date.is_(None),
        )
    ).first()


def assign_member(
    db: Session,
    project: Project,
    *,
    member_id: int,
    start_date: date,
    actor: User,
) -> ProjectAssignment:
    if project.status != "active":
        raise ServiceError("Cannot assign a member to an inactive project")
    member = db.get(Member, member_id)
    if member is None:
        raise ServiceError("Member not found")
    if not member.is_active:
        raise ServiceError("Member is not active")

    current = current_open_assignment(db, member.id)
    if current is not None:
        if current.project_id == project.id:
            raise ServiceError("That member is already assigned to this project", 409)
        if start_date <= current.start_date:
            raise ServiceError(
                "The new assignment start date must be after the current assignment start date"
            )
        current.end_date = start_date - timedelta(days=1)
        record_audit(
            db,
            actor_user_id=actor.id,
            action="project_assignment.ended",
            target_type="project_assignment",
            target_id=str(current.id),
            metadata={
                "project_id": current.project_id,
                "member_id": member.id,
                "end_date": current.end_date.isoformat(),
            },
        )

    assignment = ProjectAssignment(
        project_id=project.id,
        member_id=member.id,
        start_date=start_date,
        created_by_user_id=actor.id,
    )
    db.add(assignment)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ServiceError("A member can have only one current project assignment", 409) from exc
    record_audit(
        db,
        actor_user_id=actor.id,
        action="project_assignment.created",
        target_type="project_assignment",
        target_id=str(assignment.id),
        metadata={
            "project_id": project.id,
            "member_id": member.id,
            "start_date": start_date.isoformat(),
        },
    )
    return assignment


def close_member_assignment(
    db: Session, project: Project, *, member_id: int, end_date: date, actor: User
) -> ProjectAssignment:
    current = current_open_assignment(db, member_id)
    if current is None or current.project_id != project.id:
        raise ServiceError("There is no current assignment on this project for that member", 404)
    if end_date < current.start_date:
        raise ServiceError("End date cannot be before the assignment start date")
    current.end_date = end_date
    record_audit(
        db,
        actor_user_id=actor.id,
        action="project_assignment.ended",
        target_type="project_assignment",
        target_id=str(current.id),
        metadata={
            "project_id": project.id,
            "member_id": member_id,
            "end_date": end_date.isoformat(),
        },
    )
    return current


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ServiceError("Name is required")
    if len(cleaned) > 150:
        raise ServiceError("Name is too long")
    return cleaned


def _clean_description(description: str | None) -> str | None:
    if description is None:
        return None
    cleaned = description.strip()
    return cleaned or None


def _clean_status(status: str) -> str:
    cleaned = status.strip().casefold()
    if cleaned not in PROJECT_STATUSES:
        raise ServiceError("Status must be active or inactive")
    return cleaned


def _flush_unique(db: Session, message: str) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ServiceError(message, 409) from exc
