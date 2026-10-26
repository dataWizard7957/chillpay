"""Monthly payroll run workflow."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.extractor import ollama_client
from app.auth.dependencies import get_db, require_csrf, require_user
from app.db.models import User
from app.services.errors import ServiceError
from app.services.payroll_runs import (
    approve_run,
    create_run,
    delete_run,
    extract_run,
    get_run,
    invoice_run,
    list_runs,
    patch_entry,
    run_view,
    save_notes,
)

router = APIRouter(prefix="/runs", tags=["runs"])


class RunCreate(BaseModel):
    year: int
    month: int
    confirm: bool = False


class ExtractBody(BaseModel):
    notes_text: str | None = None


class EntryPatch(BaseModel):
    evidence: str | None = None
    total_leaves: str | None = None
    extra_work_days: str | None = None
    comp_off: str | None = None
    hours_worked: str | None = None
    overtime_hours: str | None = None
    one_off_bonus: str | None = None
    advance_amount: str | None = None


def _raise(exc: ServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("")
def get_runs(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    rows = list_runs(db, user)
    return {
        "runs": [
            {
                "id": run.id,
                "year": run.year,
                "month": run.month,
                "status": run.status,
            }
            for run in rows
        ]
    }


@router.post("")
def post_run(
    body: RunCreate,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        run = create_run(db, user, year=body.year, month=body.month, confirm=body.confirm)
    except ServiceError as exc:
        _raise(exc)
    return {"run": run_view(db, user, run)}


@router.delete("/{run_id}")
def remove_run(
    run_id: int, user: User = Depends(require_csrf), db: Session = Depends(get_db)
) -> dict:
    try:
        run = get_run(db, user, run_id)
        delete_run(db, user, run)
    except ServiceError as exc:
        _raise(exc)
    return {"deleted": True}


@router.get("/{run_id}")
def get_one_run(
    run_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict:
    try:
        run = get_run(db, user, run_id)
    except ServiceError as exc:
        _raise(exc)
    return {"run": run_view(db, user, run)}


@router.post("/{run_id}/extract")
def post_extract(
    run_id: int,
    body: ExtractBody,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        run = get_run(db, user, run_id)
        if body.notes_text is not None:
            save_notes(db, user, run, body.notes_text)
        extract_run(db, user, run, model_client=ollama_client)
    except ServiceError as exc:
        _raise(exc)
    return {"run": run_view(db, user, run)}


@router.patch("/{run_id}/entries/{member_id}")
def patch_run_entry(
    run_id: int,
    member_id: int,
    body: EntryPatch,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        run = get_run(db, user, run_id)
        fields = body.model_dump(exclude_unset=True)
        patch_entry(db, user, run, member_id, fields)
        run = get_run(db, user, run_id)
    except ServiceError as exc:
        _raise(exc)
    return {"run": run_view(db, user, run)}


@router.post("/{run_id}/approve")
def post_approve(
    run_id: int, user: User = Depends(require_csrf), db: Session = Depends(get_db)
) -> dict:
    try:
        run = get_run(db, user, run_id)
        approve_run(db, user, run)
    except ServiceError as exc:
        _raise(exc)
    return {"run": run_view(db, user, run), "message": "Approved."}


@router.post("/{run_id}/invoices")
def post_invoices(
    run_id: int, user: User = Depends(require_csrf), db: Session = Depends(get_db)
) -> dict:
    try:
        run = get_run(db, user, run_id)
        invoice_run(db, user, run)
        run = get_run(db, user, run_id)
    except ServiceError as exc:
        _raise(exc)
    return {"run": run_view(db, user, run)}
