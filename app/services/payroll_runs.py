"""Monthly payroll runs. Money is calculated only by the existing engine."""

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.extractor import ExtractionError, ModelUnavailableError, NUMBER_FIELDS, extract_payroll_note, ollama_client
from app.auth.policies import (
    can_access_member_record,
    can_approve_payroll_entry,
    can_edit_payroll_draft,
    can_write,
    current_head_assignment,
)
from app.db.models import Invoice, Member, PayrollEntry, PayrollRun, RunEntry, User, utcnow
from app.services.audit import record_audit
from app.services.errors import ServiceError
from app.services.invoices import generate_invoice
from app.services.members import list_members
from app.services.payroll_engine import calculate_payroll
from app.services.payroll_entries import _term_for_full_month
from app.services.payroll_preview import save_snapshot
from app.services.payroll_validation import PayrollValidationError, validate_payroll_inputs

ZERO = Decimal("0")
NUMBER_INPUTS = NUMBER_FIELDS
FORWARD = {
    "collect": "review",
    "review": "approve",
    "approve": "invoiced",
}


class _MemberEntry:
    def __init__(self, member_id: int) -> None:
        self.member_id = member_id


def list_runs(db: Session, user: User) -> list[PayrollRun]:
    rows = list(db.scalars(select(PayrollRun).order_by(PayrollRun.year.desc(), PayrollRun.month.desc(), PayrollRun.id.desc())))
    return [run for run in rows if _can_see_run(db, user, run)]


def get_run(db: Session, user: User, run_id: int) -> PayrollRun:
    run = db.get(PayrollRun, run_id)
    if run is None or not _can_see_run(db, user, run):
        raise ServiceError("Not found", 404)
    return run


def create_run(db: Session, user: User, *, year: int, month: int, confirm: bool = False) -> PayrollRun:
    if not can_write(user):
        raise ServiceError("Not authorized", 403)
    if year < 1 or month < 1 or month > 12:
        raise ServiceError("Month must be a calendar month")
    existing = list(
        db.scalars(select(PayrollRun).where(PayrollRun.year == year, PayrollRun.month == month))
    )
    if any(row.status == "invoiced" for row in existing):
        raise ServiceError("A run for that month is already invoiced", 409)
    if existing and not confirm:
        raise ServiceError("A run for that month already exists", 409)
    run = PayrollRun(
        year=year,
        month=month,
        status="collect",
        notes_text="",
        created_by=user.id,
    )
    db.add(run)
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="payroll_run.created",
        target_type="payroll_run",
        target_id=str(run.id),
        metadata={"from_status": None, "to_status": "collect"},
    )
    return run


def delete_run(db: Session, user: User, run: PayrollRun) -> None:
    _require_editor(user)
    if run.status == "invoiced":
        raise ServiceError("An invoiced run cannot be deleted", 409)
    for row in list(db.scalars(select(RunEntry).where(RunEntry.run_id == run.id))):
        db.delete(row)
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="payroll_run.deleted",
        target_type="payroll_run",
        target_id=str(run.id),
        metadata={"from_status": run.status, "to_status": None},
    )
    db.delete(run)
    db.flush()


def save_notes(db: Session, user: User, run: PayrollRun, notes_text: str) -> PayrollRun:
    _require_editor(user)
    if run.status != "collect":
        raise ServiceError("Notes can only be collected before review", 409)
    run.notes_text = notes_text if isinstance(notes_text, str) else ""
    run.updated_at = utcnow()
    db.flush()
    return run


def extract_run(db: Session, user: User, run: PayrollRun, *, model_client=ollama_client) -> PayrollRun:
    _require_editor(user)
    _require_status(run, "collect")
    known = _known_members(db, user)
    try:
        result = extract_payroll_note(run.notes_text, model_client=model_client, known_members=known)
    except ModelUnavailableError as exc:
        raise ServiceError(str(exc), 503) from exc
    except ExtractionError as exc:
        raise ServiceError(str(exc.errors[0] if exc.errors else str(exc)), 400) from exc
    return _apply_extraction_result(db, user, run, result)


def collect_chat_note(db: Session, user: User, note: str, result: dict) -> PayrollRun:
    """Save a chat note onto the latest open month, or start the current month."""

    run = _latest_open_run(db, user)
    if run is None:
        now = utcnow()
        try:
            run = create_run(db, user, year=now.year, month=now.month)
        except ServiceError as exc:
            if exc.status_code == 409 and "already invoiced" not in exc.message:
                run = create_run(db, user, year=now.year, month=now.month, confirm=True)
            else:
                raise
    if run.status == "collect":
        previous = (run.notes_text or "").strip()
        save_notes(db, user, run, f"{previous}\n{note}".strip() if previous else note)
        return _apply_extraction_result(db, user, run, result)
    for member, proposed in _accepted_members(db, user, result)[1]:
        fields = {field: proposed.get(field) for field in NUMBER_INPUTS}
        evidence = proposed.get("evidence")
        if isinstance(evidence, str) and evidence:
            fields["evidence"] = evidence
        patch_entry(db, user, run, member.id, fields)
    run.unmatched_names = _accepted_members(db, user, result)[0]
    return run


def _latest_open_run(db: Session, user: User) -> PayrollRun | None:
    for run in list_runs(db, user):
        if run.status in {"collect", "review"}:
            return run
    return None


def _accepted_members(db: Session, user: User, result: dict) -> tuple[list[str], list[tuple[Member, dict]]]:
    unmatched = [str(name) for name in result.get("unmatched_names") or [] if str(name).strip()]
    accepted = []
    for proposed in result.get("members") or []:
        member_id = proposed.get("member_id")
        if member_id is None:
            continue
        member = db.get(Member, member_id)
        if member is None:
            continue
        if user.role == "member" and not can_edit_payroll_draft(user, member):
            continue
        if user.role == "department_head" and not can_access_member_record(db, user, member):
            continue
        accepted.append((member, proposed))
    return unmatched, accepted


def _apply_extraction_result(db: Session, user: User, run: PayrollRun, result: dict) -> PayrollRun:
    _require_editor(user)
    _require_status(run, "collect")
    unmatched, accepted = _accepted_members(db, user, result)
    run.unmatched_names = unmatched
    if not accepted:
        run.updated_at = utcnow()
        db.flush()
        return run
    for row in list(db.scalars(select(RunEntry).where(RunEntry.run_id == run.id))):
        db.delete(row)
    db.flush()
    for member, proposed in accepted:
        entry = RunEntry(run_id=run.id, member_id=member.id, evidence=str(proposed.get("evidence") or ""))
        for field in NUMBER_INPUTS:
            setattr(entry, field, _numeric(proposed.get(field)))
        _recalculate(db, run, entry)
        db.add(entry)
    run.status = "review"
    run.updated_at = utcnow()
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="payroll_run.extracted",
        target_type="payroll_run",
        target_id=str(run.id),
        metadata={"from_status": "collect", "to_status": "review"},
    )
    return run


def patch_entry(db: Session, user: User, run: PayrollRun, member_id: int, fields: dict) -> RunEntry:
    if run.status not in {"collect", "review"}:
        raise ServiceError("An approved entry cannot be edited", 409)
    member = db.get(Member, member_id)
    if member is None or not _can_see_member(db, user, member):
        raise ServiceError("Not found", 404)
    if not can_edit_payroll_draft(user, member) and user.role != "hr":
        raise ServiceError("Not authorized", 403)
    entry = db.scalars(
        select(RunEntry).where(RunEntry.run_id == run.id, RunEntry.member_id == member_id)
    ).first()
    if entry is None:
        entry = RunEntry(run_id=run.id, member_id=member_id, evidence="")
        db.add(entry)
    if "evidence" in fields and isinstance(fields["evidence"], str):
        entry.evidence = fields["evidence"]
    for field in NUMBER_INPUTS:
        if field in fields:
            setattr(entry, field, _numeric(fields.get(field)))
    _recalculate(db, run, entry)
    if run.status == "collect":
        run.status = "review"
        record_audit(
            db,
            actor_user_id=user.id,
            action="payroll_run.extracted",
            target_type="payroll_run",
            target_id=str(run.id),
            metadata={"from_status": "collect", "to_status": "review"},
        )
    run.updated_at = utcnow()
    db.flush()
    return entry


def approve_run(db: Session, user: User, run: PayrollRun) -> PayrollRun:
    _require_status(run, "review")
    entries = _entries(db, run)
    if not entries:
        raise ServiceError("A run with no entries cannot be approved", 409)
    if any(_errors(entry) for entry in entries):
        raise ServiceError("A run with validation errors cannot be approved", 409)
    if not all(can_approve_payroll_entry(db, user, _MemberEntry(entry.member_id)) for entry in entries):
        raise ServiceError("Not authorized", 403)
    run.status = "approve"
    run.approved_by = user.id
    run.updated_at = utcnow()
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="payroll_run.approved",
        target_type="payroll_run",
        target_id=str(run.id),
        metadata={"from_status": "review", "to_status": "approve"},
    )
    return run


def invoice_run(db: Session, user: User, run: PayrollRun) -> list[Invoice]:
    if user.role not in {"hr", "department_head"}:
        raise ServiceError("Not authorized", 403)
    _require_status(run, "approve")
    entries = _entries(db, run)
    missing = _invoice_blockers(db, entries)
    if missing:
        raise ServiceError(" ".join(missing), 409)
    invoices = []
    for entry in _entries(db, run):
        payroll_entry = _sync_payroll_entry(db, run, entry, user)
        invoice = generate_invoice(db, payroll_entry, user, run_entry_id=entry.id)
        invoices.append(invoice)
    run.status = "invoiced"
    run.updated_at = utcnow()
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="payroll_run.invoiced",
        target_type="payroll_run",
        target_id=str(run.id),
        metadata={"from_status": "approve", "to_status": "invoiced"},
    )
    return invoices


def run_view(db: Session, user: User, run: PayrollRun) -> dict:
    entries = [entry for entry in _entries(db, run) if _can_see_member(db, user, db.get(Member, entry.member_id))]
    visible = [entry_view(db, entry) for entry in entries]
    errors = [item for row in visible for item in row["validation_errors"]]
    can_approve = (
        run.status == "review"
        and bool(entries)
        and not errors
        and all(can_approve_payroll_entry(db, user, _MemberEntry(entry.member_id)) for entry in entries)
    )
    invoices = []
    if run.status == "invoiced":
        from app.services.invoices import invoice_view

        for entry in entries:
            invoice = db.scalars(select(Invoice).where(Invoice.run_entry_id == entry.id)).first()
            if invoice is not None:
                invoices.append(invoice_view(db, invoice))
    return {
        "id": run.id,
        "year": run.year,
        "month": run.month,
        "status": run.status,
        "created_by": run.created_by,
        "approved_by": run.approved_by,
        "can_approve": can_approve,
        "who_needs_to_approve": None if can_approve else _who_needs_to_approve(db, user, entries, errors, run),
        "entries": visible,
        "totals": {
            "members": len(visible),
            "gross": _sum(visible, "gross"),
            "tds": _sum(visible, "tds"),
            "amount_paid": _sum(visible, "amount_paid"),
        },
        "invoices": invoices,
        "has_notes": bool((run.notes_text or "").strip()),
        "unmatched_names": list(getattr(run, "unmatched_names", []) or []),
        "approval_note": _approval_note(db, entries),
        "invoice_blockers": _invoice_blockers(db, entries),
    }


def _approval_note(db: Session, entries: list[RunEntry]) -> str | None:
    for entry in entries:
        member = db.get(Member, entry.member_id)
        if member is not None and current_head_assignment(db, member.department_id) is None:
            return "You can approve this month because no Department Head is assigned yet."
    return None


def _invoice_blockers(db: Session, entries: list[RunEntry]) -> list[str]:
    from app.services.company_settings import get_setting

    messages = []
    for key, label in (
        ("company_name", "company name"),
        ("company_address", "company address"),
        ("company_email", "company email"),
        ("company_phone", "company phone"),
    ):
        row = get_setting(db, key)
        if row is None or not row.value.strip():
            messages.append(f"Add the {label} in Settings before creating invoices.")
    for entry in entries:
        member = db.get(Member, entry.member_id)
        if member is None:
            continue
        if not (member.address or "").strip():
            messages.append(f"{member.full_name} needs an address.")
        if not (member.work_description or "").strip():
            messages.append(f"{member.full_name} needs a work description.")
    return messages


def entry_view(db: Session, entry: RunEntry) -> dict:
    member = db.get(Member, entry.member_id)
    return {
        "id": entry.id,
        "member_id": entry.member_id,
        "member_name": "" if member is None else member.full_name,
        "evidence": entry.evidence,
        "total_leaves": _plain(entry.total_leaves),
        "extra_work_days": _plain(entry.extra_work_days),
        "comp_off": _plain(entry.comp_off),
        "hours_worked": _plain(entry.hours_worked),
        "overtime_hours": _plain(entry.overtime_hours),
        "one_off_bonus": _plain(entry.one_off_bonus),
        "advance_amount": _plain(entry.advance_amount),
        "payment": _plain(entry.payment),
        "overtime_pay": _plain(entry.overtime_pay),
        "allowance": _plain(entry.allowance),
        "bonus": _plain(entry.bonus),
        "gross": _plain(entry.gross),
        "tds": _plain(entry.tds),
        "amount_paid": _plain(entry.amount_paid),
        "net_deposit": _plain(entry.net_deposit),
        "validation_errors": _errors(entry),
        "warnings": json.loads(entry.warnings_json or "[]"),
    }


def _recalculate(db: Session, run: PayrollRun, entry: RunEntry) -> None:
    member = db.get(Member, entry.member_id)
    errors: list[str] = []
    warnings: list[str] = []
    if member is None:
        entry.validation_errors_json = json.dumps(["Member not found"])
        return
    try:
        term = _term_for_full_month(db, member.id, run.year, run.month)
    except ServiceError as exc:
        entry.validation_errors_json = json.dumps([exc.message])
        entry.warnings_json = "[]"
        return
    hourly = term.pay_basis == "hourly"
    leaves = None if hourly else entry.total_leaves
    extra = None if hourly else entry.extra_work_days
    comp = None if hourly else entry.comp_off
    hours = entry.hours_worked if hourly else None
    if not hourly and entry.hours_worked not in (None, ZERO):
        hours = None
    try:
        validate_payroll_inputs(
            pay_basis=term.pay_basis,
            monthly_payment=term.monthly_payment,
            hourly_rate=term.hourly_rate,
            total_leaves=leaves,
            extra_work_days=extra,
            comp_off=comp,
            hours_worked=hours,
            overtime_hours=entry.overtime_hours,
        )
        result = calculate_payroll(
            member_type=member.member_type,
            pay_basis=term.pay_basis,
            monthly_payment=term.monthly_payment,
            hourly_rate=term.hourly_rate,
            total_leaves=leaves,
            extra_work_days=extra,
            comp_off=comp,
            hours_worked=hours,
            overtime_hours=entry.overtime_hours,
            default_project_allowance=term.default_project_allowance,
            default_project_bonus=term.default_project_bonus,
            one_off_bonus=entry.one_off_bonus,
        )
        entry.payment = result.final_payment if result.final_payment is not None else result.regular_pay
        entry.overtime_pay = result.overtime_pay
        entry.allowance = result.project_allowance
        entry.bonus = result.project_bonus
        entry.gross = result.gross_payable
        entry.tds = result.tds
        entry.amount_paid = result.amount_paid
        entry.net_deposit = result.net_deposit
        if result.recovery_shortfall_flag:
            warnings.append("Advance recovery was limited to the amount paid.")
    except PayrollValidationError as exc:
        errors.extend(exc.errors)
        entry.payment = None
        entry.overtime_pay = None
        entry.allowance = None
        entry.bonus = None
        entry.gross = None
        entry.tds = None
        entry.amount_paid = None
        entry.net_deposit = None
    entry.validation_errors_json = json.dumps(errors)
    entry.warnings_json = json.dumps(warnings)


def _sync_payroll_entry(db: Session, run: PayrollRun, entry: RunEntry, actor: User) -> PayrollEntry:
    payroll_entry = db.scalars(
        select(PayrollEntry).where(
            PayrollEntry.member_id == entry.member_id,
            PayrollEntry.year == run.year,
            PayrollEntry.month == run.month,
        )
    ).first()
    term = _term_for_full_month(db, entry.member_id, run.year, run.month)
    hourly = term.pay_basis == "hourly"
    values = {
        "total_leaves": ZERO if hourly else entry.total_leaves,
        "extra_work_days": ZERO if hourly else entry.extra_work_days,
        "comp_off": ZERO if hourly else entry.comp_off,
        "hours_worked": entry.hours_worked if hourly else None,
        "overtime_hours": entry.overtime_hours,
        "one_off_bonus": entry.one_off_bonus,
        "one_off_bonus_authorized_by_user_id": actor.id if entry.one_off_bonus else None,
    }
    if payroll_entry is None:
        payroll_entry = PayrollEntry(
            member_id=entry.member_id,
            year=run.year,
            month=run.month,
            status="approved",
            created_by_user_id=actor.id,
            **values,
        )
        db.add(payroll_entry)
        db.flush()
    else:
        for key, value in values.items():
            setattr(payroll_entry, key, value)
        if payroll_entry.status not in {"approved", "invoiced"}:
            payroll_entry.status = "approved"
        db.flush()
    save_snapshot(db, payroll_entry, actor, "approved")
    return payroll_entry


def _known_members(db: Session, user: User) -> list[dict]:
    rows = []
    for member in list_members(db):
        if can_access_member_record(db, user, member) or (
            user.role == "hr" or (user.role == "member" and can_edit_payroll_draft(user, member))
        ):
            rows.append({"id": member.id, "name": member.full_name})
    return rows


def _can_see_run(db: Session, user: User, run: PayrollRun) -> bool:
    if user.role == "hr":
        return True
    if run.created_by == user.id:
        return True
    return any(
        _can_see_member(db, user, db.get(Member, entry.member_id))
        for entry in _entries(db, run)
    )


def _can_see_member(db: Session, user: User, member: Member | None) -> bool:
    if member is None:
        return False
    return can_access_member_record(db, user, member)


def _entries(db: Session, run: PayrollRun) -> list[RunEntry]:
    return list(db.scalars(select(RunEntry).where(RunEntry.run_id == run.id).order_by(RunEntry.id)))


def _errors(entry: RunEntry) -> list[str]:
    try:
        payload = json.loads(entry.validation_errors_json or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in payload]


def _require_status(run: PayrollRun, expected: str) -> None:
    if run.status != expected:
        raise ServiceError(f"A {run.status} run cannot skip to {FORWARD.get(expected, expected)}", 409)


def _require_editor(user: User) -> None:
    if not can_write(user):
        raise ServiceError("Not authorized", 403)


def _who_needs_to_approve(db: Session, user: User, entries: list[RunEntry], errors: list[str], run: PayrollRun) -> str:
    if run.status != "review":
        return "This run is not waiting for approval."
    if errors:
        return "Fix validation errors before approval."
    if not entries:
        return "Add at least one member before approval."
    return "The department head must approve, or HR while that head is on leave. A head's own entry is approved by HR."


def _numeric(value) -> Decimal:
    if value is None or value == "" or isinstance(value, bool):
        return ZERO
    try:
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            number = Decimal(str(value))
        elif isinstance(value, str):
            number = Decimal(value.strip())
        else:
            return ZERO
    except Exception:
        return ZERO
    if number < 0:
        return ZERO
    return number


def _plain(value) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _sum(rows: list[dict], key: str) -> str:
    total = ZERO
    for row in rows:
        raw = row.get(key)
        if raw not in (None, ""):
            total += Decimal(str(raw))
    return format(total, "f")
