"""Create a company and its first HR account."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.passwords import MIN_PASSWORD_LENGTH, hash_password
from app.db.models import AppSetting, Company, Department, User
from app.services.department import INITIAL_DEPARTMENTS
from app.services.errors import ServiceError

STARTER_SETTINGS = (
    ("contract_tds_rate", "0.10"),
    ("overtime_multiplier", "1.5"),
    ("aadhaar_storage_enabled", "false"),
)


def register_company(db: Session, *, email: str, password: str, company_name: str) -> User:
    cleaned = email.strip().casefold()
    if not cleaned or "@" not in cleaned:
        raise ServiceError("A valid email is required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ServiceError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    name = " ".join(company_name.split())
    if not name:
        raise ServiceError("A company name is required")
    existing = db.scalar(select(User).where(User.email == cleaned))
    if existing is not None:
        raise ServiceError("An account with this email already exists", 409)

    company = Company(name=name)
    db.add(company)
    db.flush()
    user = User(
        company_id=company.id,
        email=cleaned,
        password_hash=hash_password(password),
        role="hr",
    )
    db.add(user)
    db.flush()
    for department_name in INITIAL_DEPARTMENTS:
        db.add(Department(company_id=company.id, name=department_name, is_active=True))
    db.add(
        AppSetting(
            company_id=company.id,
            key="company_name",
            value=name,
            updated_by_user_id=user.id,
        )
    )
    for key, value in STARTER_SETTINGS:
        db.add(
            AppSetting(
                company_id=company.id,
                key=key,
                value=value,
                updated_by_user_id=user.id,
            )
        )
    db.flush()
    return user
