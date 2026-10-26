"""Chat proposals. This route never writes payroll or approvals."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db, require_csrf, require_user
from app.db.models import User
from app.services.chat import handle_message

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatBody(BaseModel):
    message: str


@router.post("")
def post_chat(
    body: ChatBody,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    result = handle_message(db, user, body.message)
    return {"chat": result}
