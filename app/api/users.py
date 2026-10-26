"""HR-only login account management."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db, require_csrf, require_roles
from app.db.models import User
from app.services.errors import ServiceError
from app.services.users import create_user, get_user, list_users, update_user, user_view

router = APIRouter(prefix="/users", tags=["users"])


class UserWrite(BaseModel):
    email: str
    password: str
    role: str
    member_id: int | None = None


class UserPatch(BaseModel):
    role: str | None = None
    member_id: int | None = None
    is_active: bool | None = None
    password: str | None = None


@router.get("")
def get_users(
    user: User = Depends(require_roles("hr")),
    db: Session = Depends(get_db),
) -> dict:
    return {"users": [user_view(row) for row in list_users(db)]}


@router.post("")
def post_user(
    body: UserWrite,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        created = create_user(
            db,
            user,
            email=body.email,
            password=body.password,
            role=body.role,
            member_id=body.member_id,
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"user": user_view(created)}


@router.patch("/{user_id}")
def patch_user(
    user_id: int,
    body: UserPatch,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        updated = update_user(db, get_user(db, user_id), user, body.model_dump(exclude_unset=True))
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"user": user_view(updated)}
