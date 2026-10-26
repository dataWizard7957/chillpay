"""Invoices from the latest approved payroll snapshot. Regeneration keeps the number."""

import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import invoice_directory
from app.db.models import AppSetting, Invoice, InvoiceVersion, Member, PayrollEntry, User, utcnow
from app.documents.invoice_pdf import InvoiceError, build_invoice_number, generate_invoice_pdf
from app.services.audit import record_audit
from app.services.errors import ServiceError
from app.services.payroll_preview import latest_snapshot

COMPANY_KEYS = ("company_name", "company_address", "company_email", "company_phone")


def generate_invoice(
    db: Session, entry: PayrollEntry, actor: User, run_entry_id: int | None = None
) -> Invoice:
    if entry.status not in {"approved", "invoiced"}:
        raise ServiceError("Invoices require an approved payroll entry", 409)
    snapshot = latest_snapshot(db, entry.id, "approved")
    if snapshot is None:
        raise ServiceError("An approved payroll snapshot is required", 409)
    member = db.get(Member, entry.member_id)
    if member is None:
        raise ServiceError("Member not found", 404)
    existing = db.scalars(select(Invoice).where(Invoice.payroll_entry_id == entry.id)).first()
    numbers = {
        row.invoice_number
        for row in db.scalars(select(Invoice)).all()
        if existing is None or row.id != existing.id
    }
    results = json.loads(snapshot.results_json)
    invoice_date = utcnow().date()
    try:
        number = build_invoice_number(
            member.full_name,
            entry.year,
            entry.month,
            existing_numbers=numbers,
            regenerate_number=None if existing is None else existing.invoice_number,
        )
        pdf_snapshot = _pdf_snapshot(entry, member, results, number, invoice_date, _company(db))
        if existing is None:
            invoice = Invoice(
                invoice_number=number,
                payroll_entry_id=entry.id,
                run_entry_id=run_entry_id,
                member_id=member.id,
                year=entry.year,
                month=entry.month,
            )
            db.add(invoice)
            db.flush()
        else:
            invoice = existing
        version = _next_version(db, invoice.id)
        path = invoice_directory() / f"{number}_v{version}.pdf"
        generate_invoice_pdf(pdf_snapshot, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        _supersede_current(db, invoice.id)
        db.add(
            InvoiceVersion(
                invoice_id=invoice.id,
                version=version,
                payroll_snapshot_id=snapshot.id,
                status="current",
                file_path=str(path),
                file_sha256=digest,
                generated_by_user_id=actor.id,
            )
        )
        entry.status = "invoiced"
        db.flush()
    except InvoiceError as exc:
        raise ServiceError(str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise ServiceError("The invoice could not be saved", 409) from exc
    record_audit(
        db,
        actor_user_id=actor.id,
        action="invoice.generated" if version == 1 else "invoice.regenerated",
        target_type="invoice",
        target_id=str(invoice.id),
        metadata={"invoice_number": number, "version": version},
    )
    return invoice


def current_version(db: Session, invoice: Invoice) -> InvoiceVersion | None:
    return db.scalars(
        select(InvoiceVersion).where(
            InvoiceVersion.invoice_id == invoice.id, InvoiceVersion.status == "current"
        )
    ).first()


def invoice_file(db: Session, invoice: Invoice) -> Path:
    version = current_version(db, invoice)
    if version is None:
        raise ServiceError("Invoice file not found", 404)
    path = Path(version.file_path)
    root = invoice_directory().resolve()
    if root not in path.resolve().parents and path.resolve() != root:
        raise ServiceError("Invoice file not found", 404)
    if not path.is_file():
        raise ServiceError("Invoice file not found", 404)
    return path


def invoice_view(db: Session, invoice: Invoice) -> dict:
    version = current_version(db, invoice)
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "payroll_entry_id": invoice.payroll_entry_id,
        "run_entry_id": invoice.run_entry_id,
        "member_id": invoice.member_id,
        "year": invoice.year,
        "month": invoice.month,
        "version": None if version is None else version.version,
        "file_sha256": None if version is None else version.file_sha256,
    }


def _pdf_snapshot(entry, member, results: dict, number: str, invoice_date, company: dict) -> dict:
    final_leaves = results.get("final_leaves")
    if final_leaves is None:
        final_leaves = "0"
    return {
        "status": "approved",
        "year": entry.year,
        "month": entry.month,
        "member_name": member.full_name,
        "member_address": member.address or "",
        "member_email": member.email,
        "company_name": company.get("company_name") or "",
        "company_address": company.get("company_address") or "",
        "company_email": company.get("company_email") or "",
        "company_phone": company.get("company_phone") or "",
        "work_description": member.work_description or "",
        "invoice_number": number,
        "invoice_date": invoice_date,
        "final_leaves": final_leaves,
        "final_payment": results.get("final_payment"),
        "regular_pay": results.get("regular_pay"),
        "overtime_pay": results.get("overtime_pay"),
        "project_allowance": results.get("project_allowance"),
        "project_bonus": results.get("project_bonus"),
        "one_off_bonus": results.get("one_off_bonus"),
        "tds": results.get("tds"),
        "amount_paid": results.get("amount_paid"),
        "member_type": results.get("member_type"),
        "payment_line_label": results.get("payment_line_label"),
    }


def _company(db: Session) -> dict:
    values = {}
    for key in COMPANY_KEYS:
        from app.services.company_settings import get_setting

        row = get_setting(db, key)
        values[key] = "" if row is None else row.value
    return values


def _next_version(db: Session, invoice_id: int) -> int:
    from sqlalchemy import func

    current = db.scalar(
        select(func.coalesce(func.max(InvoiceVersion.version), 0)).where(
            InvoiceVersion.invoice_id == invoice_id
        )
    )
    return int(current or 0) + 1


def _supersede_current(db: Session, invoice_id: int) -> None:
    rows = db.scalars(
        select(InvoiceVersion).where(
            InvoiceVersion.invoice_id == invoice_id, InvoiceVersion.status == "current"
        )
    ).all()
    for row in rows:
        row.status = "superseded"
    if rows:
        db.flush()
