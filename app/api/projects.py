"""Project records and dated assignment history."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db, require_csrf, require_roles, require_user
from app.auth.policies import can_manage_org, can_view_project
from app.db.models import Project, ProjectAssignment, User
from app.services.errors import ServiceError
from app.services.project import (
    assign_member,
    close_member_assignment,
    create_project,
    get_project,
    list_assignments,
    list_projects,
    update_project,
)

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectWrite(BaseModel):
    name: str
    description: str | None = None
    status: str = "active"


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class AssignmentWrite(BaseModel):
    member_id: int
    start_date: date


class AssignmentClose(BaseModel):
    member_id: int
    end_date: date


def _raise_service(exc: ServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _project_payload(project: Project, assignments: list[ProjectAssignment] | None = None) -> dict:
    payload = {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "created_at": project.created_at.isoformat(sep=" "),
        "updated_at": project.updated_at.isoformat(sep=" "),
    }
    if assignments is not None:
        payload["assignments"] = [_assignment_payload(row) for row in assignments]
    return payload


def _assignment_payload(row: ProjectAssignment) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "member_id": row.member_id,
        "start_date": row.start_date.isoformat(),
        "end_date": None if row.end_date is None else row.end_date.isoformat(),
    }


def _visible_project(db: Session, user: User, project_id: int) -> Project:
    try:
        project = get_project(db, project_id)
    except ServiceError as exc:
        _raise_service(exc)
    if not can_view_project(db, user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    return project


@router.get("")
def get_projects(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    visible = [project for project in list_projects(db) if can_view_project(db, user, project)]
    return {"projects": [_project_payload(project) for project in visible]}


@router.get("/{project_id}")
def get_one_project(
    project_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict:
    project = _visible_project(db, user, project_id)
    return {"project": _project_payload(project, list_assignments(db, project.id))}


@router.post("")
def post_project(
    body: ProjectWrite,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if not can_manage_org(user):
        raise HTTPException(status_code=403, detail="Not authorized")
    try:
        project = create_project(
            db, name=body.name, description=body.description, status=body.status, actor=user
        )
    except ServiceError as exc:
        _raise_service(exc)
    return {"project": _project_payload(project)}


@router.patch("/{project_id}")
def patch_project(
    project_id: int,
    body: ProjectPatch,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        project = update_project(
            db,
            get_project(db, project_id),
            actor=user,
            name=body.name,
            description=body.description,
            status=body.status,
        )
    except ServiceError as exc:
        _raise_service(exc)
    return {"project": _project_payload(project)}


@router.post("/{project_id}/assignments")
def post_assignment(
    project_id: int,
    body: AssignmentWrite,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        assignment = assign_member(
            db,
            get_project(db, project_id),
            member_id=body.member_id,
            start_date=body.start_date,
            actor=user,
        )
    except ServiceError as exc:
        _raise_service(exc)
    return {"assignment": _assignment_payload(assignment)}


@router.post("/{project_id}/assignments/close")
def post_close_assignment(
    project_id: int,
    body: AssignmentClose,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        assignment = close_member_assignment(
            db,
            get_project(db, project_id),
            member_id=body.member_id,
            end_date=body.end_date,
            actor=user,
        )
    except ServiceError as exc:
        _raise_service(exc)
    return {"assignment": _assignment_payload(assignment)}
