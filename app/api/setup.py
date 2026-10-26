"""Unauthenticated first-time setup. Closed after the first user exists."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db
from app.services.errors import ServiceError
from app.services.setup import complete_setup, setup_is_open

router = APIRouter(prefix="/setup", tags=["setup"])


class SetupBody(BaseModel):
    email: str
    password: str
    company_name: str | None = None
    company_address: str | None = None
    company_email: str | None = None
    company_phone: str | None = None


@router.get("/status")
def setup_status(db: Session = Depends(get_db)) -> dict:
    return {"setup_required": setup_is_open(db)}


@router.post("")
def post_setup(body: SetupBody, db: Session = Depends(get_db)) -> dict:
    try:
        user = complete_setup(
            db,
            email=body.email,
            password=body.password,
            company_name=body.company_name,
            company_address=body.company_address,
            company_email=body.company_email,
            company_phone=body.company_phone,
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"user": {"id": user.id, "email": user.email, "role": user.role}, "setup_required": False}
