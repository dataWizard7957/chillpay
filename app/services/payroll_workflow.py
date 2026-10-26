"""Draft, submission, approval, rejection, and reopening.

Snapshots are written at submission and approval. They are not updated later.
"""

from sqlalchemy.orm import Session

from app.auth.policies import can_approve_payroll_entry, can_edit_payroll_draft
from app.db.models import ApprovalEvent, Member, PayrollEntry, User
from app.services.audit import record_audit
from app.services.errors import ServiceError
from app.services.payroll_preview import save_snapshot


def submit_entry(db: Session, entry: PayrollEntry, actor: User, lock_version: int) -> PayrollEntry:
    member = _member(db, entry)
    if not can_edit_payroll_draft(actor, member):
        raise ServiceError("Not authorized", 403)
    _change(db, entry, actor, lock_version, action="submitted", from_status="draft", to_status="submitted")
    save_snapshot(db, entry, actor, "submitted")
    return entry


def approve_entry(db: Session, entry: PayrollEntry, actor: User, lock_version: int) -> PayrollEntry:
    member = _member(db, entry)
    if not can_approve_payroll_entry(db, actor, entry):
        raise ServiceError("Not authorized", 403)
    _change(
        db, entry, actor, lock_version, action="approved", from_status="submitted", to_status="approved"
    )
    save_snapshot(db, entry, actor, "approved")
    return entry


def reject_entry(
    db: Session, entry: PayrollEntry, actor: User, lock_version: int, comment: str | None
) -> PayrollEntry:
    if not can_approve_payroll_entry(db, actor, entry):
        raise ServiceError("Not authorized", 403)
    _change(
        db,
        entry,
        actor,
        lock_version,
        action="rejected",
        from_status="submitted",
        to_status="rejected",
        comment=comment,
    )
    return entry


def reopen_entry(
    db: Session, entry: PayrollEntry, actor: User, lock_version: int, comment: str | None
) -> PayrollEntry:
    member = _member(db, entry)
    if entry.status == "invoiced":
        raise ServiceError("Invoiced entries cannot be reopened", 409)
    if entry.status not in {"approved", "rejected"}:
        raise ServiceError(f"A {entry.status} entry cannot be reopened", 409)
    if actor.role == "hr":
        allowed = True
    elif actor.role == "member" and actor.member_id == member.id and entry.status == "rejected":
        allowed = True
    else:
        allowed = False
    if not allowed:
        raise ServiceError("Not authorized", 403)
    _change(
        db,
        entry,
        actor,
        lock_version,
        action="reopened",
        from_status=entry.status,
        to_status="draft",
        comment=comment,
    )
    return entry


def _change(
    db: Session,
    entry: PayrollEntry,
    actor: User,
    lock_version: int,
    *,
    action: str,
    from_status: str,
    to_status: str,
    comment: str | None = None,
) -> None:
    if entry.lock_version != lock_version:
        raise ServiceError("The payroll entry was changed by someone else", 409)
    if entry.status != from_status:
        raise ServiceError(f"A {entry.status} entry cannot be {action}", 409)
    cleaned = (comment or "").strip()
    if action in {"rejected", "reopened"} and not cleaned:
        raise ServiceError("A comment is required")
    entry.status = to_status
    db.add(
        ApprovalEvent(
            payroll_entry_id=entry.id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            actor_user_id=actor.id,
            comment=cleaned or None,
        )
    )
    db.flush()
    record_audit(
        db,
        actor_user_id=actor.id,
        action=f"payroll.{action}",
        target_type="payroll_entry",
        target_id=str(entry.id),
        metadata={"from_status": from_status, "to_status": to_status},
    )


def _member(db: Session, entry: PayrollEntry) -> Member:
    member = db.get(Member, entry.member_id)
    if member is None:
        raise ServiceError("Member not found", 404)
    return member
