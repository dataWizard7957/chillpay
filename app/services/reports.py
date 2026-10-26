"""Reports built from saved snapshots and ledger rows. Nothing is recalculated here."""

import json
from calendar import monthrange
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.policies import can_access_member_record
from app.db.models import (
    Advance,
    AdvanceInstallment,
    AdvanceRecovery,
    AuditRecord,
    Department,
    Invoice,
    InvoiceVersion,
    Member,
    PayrollEntry,
    Project,
    ProjectAssignment,
    User,
)
from app.security.identifiers import mask_pan, reveal
from app.services.advances import current_schedule_version, outstanding_amount
from app.services.errors import ServiceError
from app.services.payroll_preview import latest_snapshot

REPORTS = {
    "payroll-register": "Monthly payroll register",
    "payroll-by-department": "Payroll by department",
    "payroll-by-project": "Payroll by project",
    "tds-monthly": "Monthly Contract TDS",
    "tds-financial-year": "Financial-year TDS",
    "gross-payable": "Consolidated Gross Payable",
    "advance-ledger": "Advance ledger",
    "advance-schedules": "Advance installment schedules",
    "outstanding-advances": "Outstanding advance balances",
    "recovery-shortfalls": "Insufficient or partial recovery",
    "invoices": "Invoice register",
    "audit": "Audit history",
}

# Indian financial year used only to group already-saved TDS amounts.
# It is not a tax-policy decision.
FY_START_MONTH = 4


def financial_year_bounds(financial_year: int) -> tuple[date, date]:
    if financial_year < 1:
        raise ServiceError("Financial year must be positive")
    return date(financial_year, FY_START_MONTH, 1), date(financial_year + 1, FY_START_MONTH - 1, 31)


def dashboard(db: Session, user: User) -> dict:
    members = _visible_members(db, user)
    member_ids = {member.id for member in members}
    entries = [
        entry
        for entry in db.scalars(select(PayrollEntry)).all()
        if entry.member_id in member_ids
    ]
    advances = [
        advance
        for advance in db.scalars(select(Advance)).all()
        if advance.member_id in member_ids or user.role == "finance"
    ]
    invoices = [
        invoice
        for invoice in db.scalars(select(Invoice)).all()
        if invoice.member_id in member_ids or user.role == "finance"
    ]
    today = date.today()
    return {
        "active_members": sum(1 for member in members if member.is_active),
        "current_period": {"year": today.year, "month": today.month},
        "pending_approvals": sum(1 for entry in entries if entry.status == "submitted"),
        "approved_entries": sum(1 for entry in entries if entry.status in {"approved", "invoiced"}),
        "invoices_generated": len(invoices),
        "outstanding_advances": sum(
            1 for advance in advances if outstanding_amount(db, advance) > 0
        ),
        "needs_hr_review": sum(1 for advance in advances if advance.needs_hr_review),
    }


def build_report(db: Session, user: User, name: str, *, year: int | None, month: int | None, financial_year: int | None) -> dict:
    if name not in REPORTS:
        raise ServiceError("Unknown report", 404)
    if name == "audit" and user.role != "hr":
        raise ServiceError("Not authorized", 403)
    if name in {"tds-monthly", "tds-financial-year"} and user.role not in {"hr", "finance"}:
        raise ServiceError("Not authorized", 403)
    builder = {
        "payroll-register": lambda: _payroll_rows(db, user, year, month),
        "payroll-by-department": lambda: _group(_payroll_rows(db, user, year, month), ("department_name",)),
        "payroll-by-project": lambda: _group(_payroll_rows(db, user, year, month), ("project_name",)),
        "tds-monthly": lambda: _tds_rows(db, user, year, month, None),
        "tds-financial-year": lambda: _tds_rows(db, user, None, None, financial_year),
        "gross-payable": lambda: _payroll_rows(db, user, year, month),
        "advance-ledger": lambda: _advance_rows(db, user),
        "advance-schedules": lambda: _schedule_rows(db, user),
        "outstanding-advances": lambda: [
            row for row in _advance_rows(db, user) if _positive(row["outstanding"])
        ],
        "recovery-shortfalls": lambda: _shortfall_rows(db, user),
        "invoices": lambda: _invoice_rows(db, user, year, month),
        "audit": lambda: _audit_rows(db),
    }[name]()
    headers = list(builder[0].keys()) if builder else _headers_for(name)
    return {
        "name": name,
        "title": REPORTS[name],
        "headers": headers,
        "rows": builder,
        "source": "saved snapshots and ledger rows",
    }


def _payroll_rows(db: Session, user: User, year: int | None, month: int | None) -> list[dict]:
    rows = []
    for entry, member, snapshot, results in _approved_entries(db, user, year, month):
        project = _project_for_month(db, member.id, entry.year, entry.month)
        department = db.get(Department, member.department_id)
        rows.append(
            {
                "member_id": member.id,
                "member_name": member.full_name,
                "department_name": "" if department is None else department.name,
                "project_name": "" if project is None else project.name,
                "year": entry.year,
                "month": entry.month,
                "status": entry.status,
                "gross_payable": results.get("gross_payable"),
                "tds": results.get("tds"),
                "amount_paid": results.get("amount_paid"),
                "snapshot_id": snapshot.id,
            }
        )
    return rows


def _tds_rows(
    db: Session, user: User, year: int | None, month: int | None, financial_year: int | None
) -> list[dict]:
    start = end = None
    if financial_year is not None:
        start, end = financial_year_bounds(financial_year)
    rows = []
    reveal_pan = user.role == "hr"
    for entry, member, snapshot, results in _approved_entries(db, user, year, month):
        if start is not None:
            period = date(entry.year, entry.month, 1)
            if period < start or period > date(end.year, end.month, 1):
                continue
        if member.member_type != "contract":
            continue
        rows.append(
            {
                "member_id": member.id,
                "member_name": member.full_name,
                "pan": _pan_for_report(member, reveal_pan),
                "year": entry.year,
                "month": entry.month,
                "gross_payable": results.get("gross_payable"),
                "tds": results.get("tds"),
                "amount_paid": results.get("amount_paid"),
                "snapshot_id": snapshot.id,
            }
        )
    return rows


def _advance_rows(db: Session, user: User) -> list[dict]:
    rows = []
    for advance in db.scalars(select(Advance).order_by(Advance.id)).all():
        member = db.get(Member, advance.member_id)
        if member is None or not _can_see(db, user, member):
            continue
        outstanding = outstanding_amount(db, advance)
        rows.append(
            {
                "advance_id": advance.id,
                "member_id": member.id,
                "member_name": member.full_name,
                "original_amount": format(advance.original_amount, "f"),
                "date_paid": advance.date_paid.isoformat(),
                "status": advance.status,
                "needs_hr_review": advance.needs_hr_review,
                "outstanding": format(outstanding, "f"),
            }
        )
    return rows


def _schedule_rows(db: Session, user: User) -> list[dict]:
    rows = []
    for advance in db.scalars(select(Advance).order_by(Advance.id)).all():
        member = db.get(Member, advance.member_id)
        if member is None or not _can_see(db, user, member):
            continue
        version = current_schedule_version(db, advance.id)
        installments = db.scalars(
            select(AdvanceInstallment)
            .where(
                AdvanceInstallment.advance_id == advance.id,
                AdvanceInstallment.schedule_version == version,
            )
            .order_by(AdvanceInstallment.sequence)
        ).all()
        for row in installments:
            rows.append(
                {
                    "advance_id": advance.id,
                    "member_name": member.full_name,
                    "schedule_version": version,
                    "sequence": row.sequence,
                    "due_year": row.due_year,
                    "due_month": row.due_month,
                    "amount": format(row.amount, "f"),
                }
            )
    return rows


def _shortfall_rows(db: Session, user: User) -> list[dict]:
    rows = []
    for recovery in db.scalars(select(AdvanceRecovery).order_by(AdvanceRecovery.id)).all():
        advance = db.get(Advance, recovery.advance_id)
        if advance is None:
            continue
        member = db.get(Member, advance.member_id)
        if member is None or not _can_see(db, user, member):
            continue
        if recovery.shortfall_amount <= 0:
            continue
        rows.append(
            {
                "advance_id": advance.id,
                "member_name": member.full_name,
                "payroll_entry_id": recovery.payroll_entry_id,
                "scheduled_amount": format(recovery.scheduled_amount, "f"),
                "recovered_amount": format(recovery.recovered_amount, "f"),
                "shortfall_amount": format(recovery.shortfall_amount, "f"),
            }
        )
    return rows


def _invoice_rows(db: Session, user: User, year: int | None, month: int | None) -> list[dict]:
    rows = []
    query = select(Invoice).order_by(Invoice.id)
    for invoice in db.scalars(query).all():
        if year is not None and invoice.year != year:
            continue
        if month is not None and invoice.month != month:
            continue
        member = db.get(Member, invoice.member_id)
        if member is None or not _can_see(db, user, member):
            continue
        version = db.scalars(
            select(InvoiceVersion).where(
                InvoiceVersion.invoice_id == invoice.id, InvoiceVersion.status == "current"
            )
        ).first()
        rows.append(
            {
                "invoice_number": invoice.invoice_number,
                "member_name": member.full_name,
                "year": invoice.year,
                "month": invoice.month,
                "version": None if version is None else version.version,
                "file_sha256": None if version is None else version.file_sha256,
            }
        )
    return rows


def _audit_rows(db: Session) -> list[dict]:
    return [
        {
            "id": row.id,
            "actor_user_id": row.actor_user_id,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "created_at": row.created_at.isoformat(sep=" "),
        }
        for row in db.scalars(select(AuditRecord).order_by(AuditRecord.id)).all()
    ]


def _approved_entries(db: Session, user: User, year: int | None, month: int | None):
    rows = []
    query = select(PayrollEntry).order_by(PayrollEntry.year, PayrollEntry.month, PayrollEntry.id)
    for entry in db.scalars(query).all():
        if year is not None and entry.year != year:
            continue
        if month is not None and entry.month != month:
            continue
        if entry.status not in {"approved", "invoiced"}:
            continue
        member = db.get(Member, entry.member_id)
        if member is None or not _can_see(db, user, member):
            continue
        snapshot = latest_snapshot(db, entry.id, "approved")
        if snapshot is None:
            continue
        results = json.loads(snapshot.results_json)
        rows.append((entry, member, snapshot, results))
    return rows


def _project_for_month(db: Session, member_id: int, year: int, month: int) -> Project | None:
    last_day = monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, last_day)
    assignment = db.scalars(
        select(ProjectAssignment).where(
            ProjectAssignment.member_id == member_id,
            ProjectAssignment.start_date <= end,
            or_(ProjectAssignment.end_date.is_(None), ProjectAssignment.end_date >= start),
        )
    ).first()
    if assignment is None:
        return None
    return db.get(Project, assignment.project_id)


def _visible_members(db: Session, user: User) -> list[Member]:
    if user.role == "finance":
        return []
    return [member for member in db.scalars(select(Member)).all() if can_access_member_record(db, user, member)]


def _can_see(db: Session, user: User, member: Member) -> bool:
    if user.role == "finance":
        return True
    return can_access_member_record(db, user, member)


def _pan_for_report(member: Member, reveal_full: bool) -> str:
    if not member.pan_ciphertext:
        return ""
    try:
        value = reveal(member.pan_ciphertext)
    except Exception:
        return ""
    return value if reveal_full else mask_pan(value)


def _group(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    grouped: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(row.get(name) for name in keys)
        bucket = grouped.setdefault(
            key,
            {
                **{name: row.get(name) for name in keys},
                "entries": 0,
                "gross_payable": "0.00",
                "tds": "0.00",
                "amount_paid": "0.00",
            },
        )
        bucket["entries"] += 1
        for field in ("gross_payable", "tds", "amount_paid"):
            bucket[field] = _add(bucket[field], row.get(field))
    return list(grouped.values())


def _add(left, right) -> str:
    from decimal import Decimal

    return format((Decimal(left or "0") + Decimal(right or "0")), "f")


def _positive(value) -> bool:
    from decimal import Decimal

    return Decimal(value or "0") > 0


def _headers_for(name: str) -> list[str]:
    return {
        "payroll-register": [
            "member_id",
            "member_name",
            "department_name",
            "project_name",
            "year",
            "month",
            "status",
            "gross_payable",
            "tds",
            "amount_paid",
            "snapshot_id",
        ],
        "audit": ["id", "actor_user_id", "action", "target_type", "target_id", "created_at"],
    }.get(name, [])
