"""Invoice generation and download. Files stay inside the invoice directory."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db, require_csrf, require_user
from app.auth.policies import can_access_member_record
from app.db.models import Invoice, User
from app.services.errors import ServiceError
from app.services.invoices import generate_invoice, invoice_file, invoice_view
from app.services.members import get_member
from app.services.payroll_entries import get_entry

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _raise_service(exc: ServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _can_read_invoice(db: Session, user: User, invoice: Invoice) -> bool:
    if user.role == "finance":
        return True
    try:
        member = get_member(db, invoice.member_id)
    except ServiceError as exc:
        _raise_service(exc)
    return can_access_member_record(db, user, member)


@router.get("")
def get_invoices(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Invoice).order_by(Invoice.id)).all()
    visible = [row for row in rows if _can_read_invoice(db, user, row)]
    return {"invoices": [invoice_view(db, row) for row in visible]}


@router.get("/{invoice_id}")
def get_one_invoice(
    invoice_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or not _can_read_invoice(db, user, invoice):
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"invoice": invoice_view(db, invoice)}


@router.get("/{invoice_id}/file")
def download_invoice(
    invoice_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> FileResponse:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or not _can_read_invoice(db, user, invoice):
        raise HTTPException(status_code=404, detail="Invoice not found")
    try:
        path = invoice_file(db, invoice)
    except ServiceError as exc:
        _raise_service(exc)
    return FileResponse(path, media_type="application/pdf", filename=f"{invoice.invoice_number}.pdf")


@router.get("/{invoice_id}/pdf")
def download_invoice_pdf(
    invoice_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> FileResponse:
    return download_invoice(invoice_id, user, db)


def generate_entry_invoice(db: Session, user: User, entry_id: int) -> dict:
    if user.role != "hr":
        raise HTTPException(status_code=403, detail="Not authorized")
    try:
        entry = get_entry(db, entry_id)
        invoice = generate_invoice(db, entry, user)
    except ServiceError as exc:
        _raise_service(exc)
    return {"invoice": invoice_view(db, invoice)}
