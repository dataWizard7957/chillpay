"""HR-only audit history. Records cannot be edited through this API."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db, require_roles
from app.db.models import AuditRecord, User

router = APIRouter(tags=["audit"])


@router.get("/audit")
def list_audit_records(
    user: User = Depends(require_roles("hr")),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.scalars(
        select(AuditRecord).order_by(AuditRecord.created_at.desc(), AuditRecord.id.desc())
    ).all()
    return {
        "records": [
            {
                "id": row.id,
                "actor_user_id": row.actor_user_id,
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "metadata": row.metadata_json,
                "created_at": row.created_at.isoformat(sep=" "),
            }
            for row in rows
        ]
    }
