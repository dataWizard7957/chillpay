"""Local note extraction. The result is never saved from this endpoint."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.extractor import (
    ExtractionError,
    ModelUnavailableError,
    extract_payroll_note,
    ollama_client,
    warmup_for_monthly_run,
)
from app.auth.dependencies import get_db, require_csrf, require_user
from app.auth.policies import can_edit_payroll_draft
from app.db.models import Member, User
from app.services.members import list_members

router = APIRouter(prefix="/extract", tags=["extract"])


class ExtractBody(BaseModel):
    note: str


@router.get("/warmup")
def get_warmup(user: User = Depends(require_user)) -> dict:
    if user.role == "finance":
        raise HTTPException(status_code=403, detail="Not authorized")
    warmup_for_monthly_run()
    return {"started": True}


@router.post("")
def post_extract(
    body: ExtractBody,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if user.role == "finance":
        raise HTTPException(status_code=403, detail="Not authorized")
    members = [
        {"id": member.id, "name": member.full_name}
        for member in list_members(db)
        if can_edit_payroll_draft(user, member)
    ]
    try:
        result = extract_payroll_note(body.note, model_client=ollama_client, known_members=members)
    except ModelUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ExtractionError as exc:
        raise HTTPException(status_code=400, detail=exc.errors[0]) from exc
    payload = {
        "original_note": result["original_note"],
        "fields": {key: _plain(value) for key, value in result["fields"].items()},
        "validation_errors": result["validation_errors"],
        "member_match": result["member_match"],
        "member_id": result["member_id"],
        "requires_human_review": True,
        "saved": False,
        "submitted": False,
    }
    return {"extraction": payload}


def _plain(value) -> str:
    return format(value, "f") if hasattr(value, "as_tuple") else str(value)
