"""Per-company settings. A missing company uses the original company, id 1."""

from sqlalchemy.orm import Session

from app.db.models import AppSetting, utcnow


def active_company_id(db: Session, company_id: int | None = None) -> int:
    if company_id is not None:
        return company_id
    return db.info.get("company_id") or 1


def get_setting(db: Session, key: str, company_id: int | None = None) -> AppSetting | None:
    return db.get(AppSetting, (active_company_id(db, company_id), key))


def put_setting(
    db: Session,
    key: str,
    value: str,
    *,
    company_id: int | None = None,
    user_id: int | None = None,
) -> AppSetting:
    cid = active_company_id(db, company_id)
    row = db.get(AppSetting, (cid, key))
    if row is None:
        row = AppSetting(company_id=cid, key=key, value=value, updated_by_user_id=user_id)
        db.add(row)
    else:
        row.value = value
        row.updated_at = utcnow()
        row.updated_by_user_id = user_id
    return row
