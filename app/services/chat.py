"""Chat prepares questions and proposed actions. It does not calculate or approve."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.extractor import ExtractionError, ModelUnavailableError, extract_payroll_note, ollama_client
from app.auth.policies import can_access_member_record, can_approve_payroll_entry
from app.db.models import Member, PayrollEntry, User
from app.services.errors import ServiceError
from app.services.members import list_members
from app.services.payroll_preview import calculation_view, preview_entry
from app.services.payroll_runs import _known_members, collect_chat_note
from app.services.reports import dashboard

FORBIDDEN = (
    "i calculated",
    "i approve",
    "approved it",
    "tds is",
    "net deposit is",
)


def handle_message(db: Session, user: User, message: str) -> dict:
    text = (message or "").strip()
    if not text:
        return _reply("Ask about a member, a payroll summary, or a note to extract.")
    lowered = text.casefold()
    if user.role == "finance" and "dashboard" not in lowered and "summary" not in lowered:
        return _reply("Finance accounts can view the dashboard and reports. I cannot preview payroll or extract notes.")
    if any(token in lowered for token in ("approve", "reject", "reopen")) and "how" not in lowered:
        return _reply(
            "Approval is a human action. Open Payroll or Approvals and confirm the change there. I cannot approve or reject."
        )
    if "dashboard" in lowered or "summary" in lowered:
        board = dashboard(db, user)
        return {
            "reply": "These counts come from the application records.",
            "proposed_action": None,
            "calculation": None,
            "dashboard": board,
            "extraction": None,
            "requires_human_review": False,
        }
    if "preview" in lowered or "how much" in lowered or "amount paid" in lowered:
        return _preview_if_possible(db, user, text)
    if _looks_like_payroll_note(text):
        return _extract_or_help(db, user, text)
    return _reply(
        "I can help with payroll notes, dashboards, and saved-entry previews. "
        "Paste a leave or hours note to extract fields, or open Payroll to enter values yourself."
    )


def _preview_if_possible(db: Session, user: User, text: str) -> dict:
    member = _guess_member(db, user, text)
    if member is None:
        return _reply("Name the member whose saved entry you want previewed. The backend will calculate.")
    entry = db.scalars(
        select(PayrollEntry)
        .where(PayrollEntry.member_id == member.id)
        .order_by(PayrollEntry.year.desc(), PayrollEntry.month.desc())
    ).first()
    if entry is None:
        return _reply(f"No payroll entry exists for {member.full_name}.")
    if not can_access_member_record(db, user, member):
        return _reply("You are not authorized to see that member.")
    result, _term, _inputs = preview_entry(db, entry)
    view = calculation_view(result)
    return {
        "reply": f"Backend preview for {member.full_name} {entry.year}-{entry.month}. I did not calculate these figures.",
        "proposed_action": {
            "type": "show_preview",
            "payroll_entry_id": entry.id,
        },
        "calculation": view,
        "extraction": None,
        "requires_human_review": False,
        "can_approve": can_approve_payroll_entry(db, user, entry),
    }


def _extract_or_help(db: Session, user: User, text: str) -> dict:
    try:
        result = extract_payroll_note(
            text, model_client=ollama_client, known_members=_known_members(db, user)
        )
    except ModelUnavailableError as exc:
        return {
            "reply": str(exc),
            "proposed_action": {"type": "manual_entry"},
            "calculation": None,
            "extraction": None,
            "requires_human_review": False,
            "model_unavailable": True,
            "fallback": "manual_entry",
        }
    except ExtractionError as exc:
        return _reply(str(exc.errors[0] if exc.errors else exc))
    try:
        run = collect_chat_note(db, user, text, result)
    except ServiceError as exc:
        return _reply(exc.message)
    fields = {key: _plain(value) for key, value in result["fields"].items()}
    member = _guess_member(db, user, str(fields.get("member_name") or text))
    lines = [f"{key}: {value}" for key, value in fields.items()]
    if lines:
        reply = "Suggested fields from the note:\n" + "\n".join(lines)
    else:
        reply = "No payroll fields were read from that note."
    if "salary" in text.casefold():
        reply += "\nSalary in the note is not saved. Pay comes from the member's saved terms."
    unmatched = [str(name) for name in result.get("unmatched_names") or [] if str(name).strip()]
    if unmatched:
        reply += "\nNot matched: " + ", ".join(unmatched) + "."
    reply += (
        f"\nSaved on the {_month_name(run.month)} {run.year} run. "
        "Open Monthly run to review it. After you approve, the invoice is listed under Invoices."
    )
    return {
        "reply": reply,
        "proposed_action": {
            "type": "open_monthly_run",
            "run_id": run.id,
            "year": run.year,
            "month": run.month,
            "fields": fields,
            "member_id": None if member is None else member.id,
            "member_match": "unassigned" if member is None else "matched",
        },
        "calculation": None,
        "extraction": {
            "original_note": result["original_note"],
            "fields": fields,
            "validation_errors": result["validation_errors"],
            "requires_human_review": True,
            "saved": run.status == "review",
        },
        "requires_human_review": True,
    }


def _month_name(month: int) -> str:
    names = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
    if 1 <= month <= 12:
        return names[month - 1]
    return str(month)


def _looks_like_payroll_note(text: str) -> bool:
    lowered = text.casefold()
    hints = (
        "leave",
        "leaves",
        "chutti",
        "hours",
        "overtime",
        "comp-off",
        "comp off",
        "allowance",
        "bonus",
        "extract",
        "advance",
        "din",
    )
    return any(hint in lowered for hint in hints) or len(text.strip()) >= 40


def _guess_member(db: Session, user: User, text: str) -> Member | None:
    matches = []
    lowered = text.casefold()
    for member in list_members(db):
        if member.full_name.casefold() in lowered and can_access_member_record(db, user, member):
            matches.append(member)
    if len(matches) == 1:
        return matches[0]
    return None


def _plain(value) -> str:
    return format(value, "f") if hasattr(value, "as_tuple") else str(value)


def _reply(text: str) -> dict:
    return {
        "reply": text,
        "proposed_action": None,
        "calculation": None,
        "extraction": None,
        "requires_human_review": False,
    }
