"""HR management of login accounts. Roles are stored on the user row."""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ROLES, Member, User, utcnow
from app.auth.passwords import MIN_PASSWORD_LENGTH, hash_password
from app.services.audit import record_audit
from app.services.errors import ServiceError


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id)))


def get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ServiceError("User not found", 404)
    return user


def create_user(
    db: Session,
    actor: User,
    *,
    email: str,
    password: str,
    role: str,
    member_id: int | None = None,
) -> User:
    cleaned_role = _clean_role(role)
    cleaned_email = email.strip().casefold()
    if not cleaned_email or "@" not in cleaned_email:
        raise ServiceError("A valid email is required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ServiceError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    member = _require_member(db, cleaned_role, member_id)
    user = User(
        company_id=actor.company_id,
        email=cleaned_email,
        password_hash=hash_password(password),
        role=cleaned_role,
        member_id=None if member is None else member.id,
    )
    db.add(user)
    _flush_unique(db)
    record_audit(
        db,
        actor_user_id=actor.id,
        action="user.created",
        target_type="user",
        target_id=str(user.id),
        metadata={"role": user.role, "member_id": user.member_id},
    )
    return user


def update_user(db: Session, user: User, actor: User, changes: dict) -> User:
    if "role" in changes and changes["role"] is not None:
        user.role = _clean_role(changes["role"])
    if "member_id" in changes:
        member = _require_member(db, user.role, changes["member_id"])
        user.member_id = None if member is None else member.id
    if "is_active" in changes and changes["is_active"] is not None:
        if changes["is_active"] is False and user.role == "hr" and _hr_count(db) <= 1:
            raise ServiceError("The last HR account cannot be disabled", 409)
        user.is_active = bool(changes["is_active"])
    if "password" in changes and changes["password"]:
        if len(changes["password"]) < MIN_PASSWORD_LENGTH:
            raise ServiceError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
        user.password_hash = hash_password(changes["password"])
    if user.role in {"member", "department_head"} and user.member_id is None:
        raise ServiceError("That role must be linked to a member")
    user.updated_at = utcnow()
    _flush_unique(db)
    record_audit(
        db,
        actor_user_id=actor.id,
        action="user.updated",
        target_type="user",
        target_id=str(user.id),
        metadata={"role": user.role, "member_id": user.member_id, "is_active": user.is_active},
    )
    return user


def user_view(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "member_id": user.member_id,
        "is_active": user.is_active,
    }


def _clean_role(role: str) -> str:
    cleaned = role.strip().casefold()
    if cleaned not in ROLES:
        raise ServiceError("Role must be hr, department_head, member, or finance")
    return cleaned


def _require_member(db: Session, role: str, member_id: int | None) -> Member | None:
    if role in {"member", "department_head"}:
        if member_id is None:
            raise ServiceError("That role must be linked to a member")
        member = db.get(Member, member_id)
        if member is None:
            raise ServiceError("Member not found", 404)
        return member
    if member_id is None:
        return None
    member = db.get(Member, member_id)
    if member is None:
        raise ServiceError("Member not found", 404)
    return member


def _hr_count(db: Session) -> int:
    return int(
        db.scalar(select(func.count()).select_from(User).where(User.role == "hr", User.is_active.is_(True)))
        or 0
    )


def _flush_unique(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        message = str(exc.orig)
        if "email" in message:
            raise ServiceError("A user with this email already exists", 409) from exc
        if "member_id" in message:
            raise ServiceError("That member already has a login", 409) from exc
        raise ServiceError("The user could not be saved", 409) from exc
