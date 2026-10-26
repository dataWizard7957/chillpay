"""Company and payroll settings. Missing company identity stays blank."""

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db, require_csrf, require_user
from app.db.models import User
from app.services.audit import record_audit
from app.services.company_settings import get_setting, put_setting

router = APIRouter(prefix="/settings", tags=["settings"])

READABLE_KEYS = (
    "company_name",
    "company_address",
    "company_email",
    "company_phone",
    "contract_tds_rate",
    "overtime_multiplier",
    "aadhaar_storage_enabled",
)
WRITABLE_KEYS = {
    "company_name",
    "company_address",
    "company_email",
    "company_phone",
    "contract_tds_rate",
    "overtime_multiplier",
}
RATE_KEYS = {"contract_tds_rate", "overtime_multiplier"}


class SettingsPatch(BaseModel):
    company_name: str | None = None
    company_address: str | None = None
    company_email: str | None = None
    company_phone: str | None = None
    contract_tds_rate: str | None = None
    overtime_multiplier: str | None = None
    aadhaar_storage_enabled: str | None = None


def _values(db: Session) -> dict:
    return {key: ("" if (row := get_setting(db, key)) is None else row.value) for key in READABLE_KEYS}


@router.get("")
def get_settings(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    if user.role != "hr":
        raise HTTPException(status_code=403, detail="Not authorized")
    return {"settings": _values(db)}


@router.patch("")
def patch_settings(
    body: SettingsPatch,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if user.role != "hr":
        raise HTTPException(status_code=403, detail="Not authorized")
    changes = body.model_dump(exclude_unset=True)
    if "aadhaar_storage_enabled" in changes:
        raise HTTPException(
            status_code=400,
            detail="Aadhaar storage stays off until its implications are documented.",
        )
    unknown = set(changes) - WRITABLE_KEYS
    if unknown:
        raise HTTPException(status_code=400, detail="That setting cannot be changed")
    for key, value in changes.items():
        cleaned = _clean_setting(key, value)
        put_setting(db, key, cleaned, user_id=user.id)
        record_audit(
            db,
            actor_user_id=user.id,
            action="setting.updated",
            target_type="app_setting",
            target_id=key,
            metadata={"key": key},
        )
    return {"settings": _values(db)}


def _clean_setting(key: str, value: str | None) -> str:
    if value is None:
        return ""
    cleaned = value.strip()
    if key not in RATE_KEYS:
        return cleaned
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail=f"{key} is not a valid rate") from exc
    if amount < 0 or amount != amount.quantize(Decimal("0.0001")):
        raise HTTPException(status_code=400, detail=f"{key} is not a valid rate")
    return format(amount, "f")
