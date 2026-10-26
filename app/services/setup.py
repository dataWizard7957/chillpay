"""One-time company setup. Disabled after the first user exists."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.passwords import MIN_PASSWORD_LENGTH, hash_password
from app.db.models import AppSetting, User, utcnow
from app.services.audit import record_audit
from app.services.errors import ServiceError

COMPANY_KEYS = ("company_name", "company_address", "company_email", "company_phone")


def setup_is_open(db: Session) -> bool:
    return (db.scalar(select(func.count()).select_from(User)) or 0) == 0


def complete_setup(
    db: Session,
    *,
    email: str,
    password: str,
    company_name: str | None = None,
    company_address: str | None = None,
    company_email: str | None = None,
    company_phone: str | None = None,
) -> User:
    if not setup_is_open(db):
        raise ServiceError("First-time setup is disabled", 403)
    cleaned = email.strip().casefold()
    if not cleaned or "@" not in cleaned:
        raise ServiceError("A valid admin email is required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ServiceError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    provided = {
        "company_name": company_name,
        "company_address": company_address,
        "company_email": company_email,
        "company_phone": company_phone,
    }
    for key, value in provided.items():
        if value is None:
            continue
        text = value.strip()
        if not text:
            continue
        from app.services.company_settings import put_setting

        put_setting(db, key, text)
    user = User(email=cleaned, password_hash=hash_password(password), role="hr")
    db.add(user)
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="setup.completed",
        target_type="user",
        target_id=str(user.id),
        metadata={"role": "hr"},
    )
    return user
