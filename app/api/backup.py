"""HR backup and restore. Restore never writes the live database path."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import require_csrf, require_roles
from app.config import backup_directory
from app.db.models import User
from app.services.backup import create_backup, restore_backup
from app.services.errors import ServiceError

router = APIRouter(prefix="/backup", tags=["backup"])


class BackupBody(BaseModel):
    destination: str | None = None


class RestoreBody(BaseModel):
    backup_folder: str
    target_database: str
    target_invoices: str


@router.post("")
def post_backup(
    body: BackupBody,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
) -> dict:
    destination = Path(body.destination) if body.destination else backup_directory()
    try:
        folder = create_backup(destination)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"backup_folder": str(folder), "actor": user.email}


@router.post("/restore")
def post_restore(
    body: RestoreBody,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
) -> dict:
    try:
        restore_backup(Path(body.backup_folder), Path(body.target_database), Path(body.target_invoices))
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"restored_database": body.target_database, "actor": user.email}
