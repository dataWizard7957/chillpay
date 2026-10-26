"""Member records. Pay amounts live only in pay-term history."""

import re
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import MEMBER_TYPES, AppSetting, Department, Member, Project, User, utcnow
from app.security.identifiers import (
    IdentifierError,
    mask_aadhaar,
    mask_pan,
    normalize_aadhaar,
    normalize_pan,
    protect,
    reveal,
)
from app.services.audit import record_audit
from app.services.errors import ServiceError
from app.services.pay_terms import add_pay_term, current_pay_term
from app.services.project import current_open_assignment

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def list_members(db: Session) -> list[Member]:
    return list(db.scalars(select(Member).order_by(Member.full_name, Member.id)))


def get_member(db: Session, member_id: int) -> Member:
    member = db.get(Member, member_id)
    if member is None:
        raise ServiceError("Member not found", 404)
    return member


def create_member(db: Session, actor: User, fields: dict, pay_term: dict) -> Member:
    member = Member(
        full_name=_clean_name(fields["full_name"]),
        email=_clean_email(fields["email"]),
        department_id=_active_department_id(db, fields["department_id"]),
        member_type=_clean_member_type(fields["member_type"]),
        joining_date=fields["joining_date"],
        last_working_date=fields.get("last_working_date"),
        notice_period_days=_clean_notice(fields.get("notice_period_days")),
        mobile=_clean_mobile(fields.get("mobile")),
        address=_clean_optional(fields.get("address")),
        emergency_contact=_clean_optional(fields.get("emergency_contact")),
        work_description=_clean_optional(fields.get("work_description")),
        is_active=bool(fields.get("is_active", True)),
    )
    _check_dates(member.joining_date, member.last_working_date)
    _apply_identifiers(
        db,
        member,
        pan=fields["pan"] if "pan" in fields else _KEEP,
        aadhaar=fields["aadhaar"] if "aadhaar" in fields else _KEEP,
    )
    db.add(member)
    _flush_member(db)
    add_pay_term(db, member, actor=actor, **pay_term)
    record_audit(
        db,
        actor_user_id=actor.id,
        action="member.created",
        target_type="member",
        target_id=str(member.id),
        metadata={
            "department_id": member.department_id,
            "member_type": member.member_type,
            "is_active": member.is_active,
        },
    )
    return member


def update_member(db: Session, member: Member, actor: User, changes: dict) -> Member:
    if not changes:
        raise ServiceError("No member fields to update")
    changed: list[str] = []
    if "full_name" in changes:
        member.full_name = _clean_name(changes["full_name"])
        changed.append("full_name")
    if "email" in changes:
        member.email = _clean_email(changes["email"])
        changed.append("email")
    if "department_id" in changes:
        member.department_id = _active_department_id(db, changes["department_id"])
        changed.append("department_id")
    if "member_type" in changes:
        member.member_type = _clean_member_type(changes["member_type"])
        changed.append("member_type")
    if "joining_date" in changes:
        member.joining_date = changes["joining_date"]
        changed.append("joining_date")
    if "last_working_date" in changes:
        member.last_working_date = changes["last_working_date"]
        changed.append("last_working_date")
    if "notice_period_days" in changes:
        member.notice_period_days = _clean_notice(changes["notice_period_days"])
        changed.append("notice_period_days")
    if "mobile" in changes:
        member.mobile = _clean_mobile(changes["mobile"])
        changed.append("mobile")
    if "address" in changes:
        member.address = _clean_optional(changes["address"])
        changed.append("address")
    if "emergency_contact" in changes:
        member.emergency_contact = _clean_optional(changes["emergency_contact"])
        changed.append("emergency_contact")
    if "work_description" in changes:
        member.work_description = _clean_optional(changes["work_description"])
        changed.append("work_description")
    if "is_active" in changes:
        member.is_active = bool(changes["is_active"])
        changed.append("is_active")
    if "pan" in changes or "aadhaar" in changes:
        _apply_identifiers(
            db,
            member,
            pan=changes["pan"] if "pan" in changes else _KEEP,
            aadhaar=changes["aadhaar"] if "aadhaar" in changes else _KEEP,
        )
        if "pan" in changes:
            changed.append("pan")
        if "aadhaar" in changes:
            changed.append("aadhaar")
    _check_dates(member.joining_date, member.last_working_date)
    member.updated_at = utcnow()
    _flush_member(db)
    if member.last_working_date is not None:
        from app.services.advances import flag_outstanding_advances

        flag_outstanding_advances(db, member)
    record_audit(
        db,
        actor_user_id=actor.id,
        action="member.updated",
        target_type="member",
        target_id=str(member.id),
        metadata={"fields": changed},
    )
    return member


def member_view(db: Session, member: Member, *, reveal_identifiers: bool, on_date: date) -> dict:
    assignment = current_open_assignment(db, member.id)
    project = None if assignment is None else db.get(Project, assignment.project_id)
    term = current_pay_term(db, member.id, on_date)
    return {
        "id": member.id,
        "full_name": member.full_name,
        "email": member.email,
        "department_id": member.department_id,
        "member_type": member.member_type,
        "joining_date": member.joining_date.isoformat(),
        "last_working_date": None
        if member.last_working_date is None
        else member.last_working_date.isoformat(),
        "notice_period_days": member.notice_period_days,
        "mobile": member.mobile,
        "address": member.address if reveal_identifiers else None,
        "emergency_contact": member.emergency_contact if reveal_identifiers else None,
        "work_description": member.work_description,
        "is_active": member.is_active,
        "pan": _display_pan(member, reveal_identifiers),
        "aadhaar": _display_aadhaar(member, reveal_identifiers),
        "current_project": None
        if project is None
        else {
            "project_id": project.id,
            "name": project.name,
            "start_date": assignment.start_date.isoformat(),
        },
        "current_pay_term": None if term is None else pay_term_view(term),
    }


def pay_term_view(term) -> dict:
    return {
        "id": term.id,
        "effective_from": term.effective_from.isoformat(),
        "effective_to": None if term.effective_to is None else term.effective_to.isoformat(),
        "pay_basis": term.pay_basis,
        "monthly_payment": _money(term.monthly_payment),
        "hourly_rate": _money(term.hourly_rate),
        "default_project_allowance": _money(term.default_project_allowance),
        "default_project_bonus": _money(term.default_project_bonus),
    }


class _Keep:
    pass


_KEEP = _Keep()


def aadhaar_storage_enabled(db: Session) -> bool:
    from app.services.company_settings import get_setting

    setting = get_setting(db, "aadhaar_storage_enabled")
    return setting is not None and setting.value.strip().casefold() == "true"


def _apply_identifiers(db: Session, member: Member, *, pan, aadhaar) -> None:
    if not isinstance(pan, _Keep):
        if pan is None or str(pan).strip() == "":
            member.pan_ciphertext = None
            member.pan_lookup = None
        else:
            try:
                normalized = normalize_pan(str(pan))
                member.pan_ciphertext, member.pan_lookup = protect(normalized)
            except IdentifierError as exc:
                raise ServiceError(exc.message, exc.status_code) from exc
    if not isinstance(aadhaar, _Keep):
        if aadhaar is None or str(aadhaar).strip() == "":
            member.aadhaar_ciphertext = None
            member.aadhaar_lookup = None
        elif not aadhaar_storage_enabled(db):
            raise ServiceError("Aadhaar storage is disabled")
        else:
            try:
                normalized = normalize_aadhaar(str(aadhaar))
                member.aadhaar_ciphertext, member.aadhaar_lookup = protect(normalized)
            except IdentifierError as exc:
                raise ServiceError(exc.message, exc.status_code) from exc


def _display_pan(member: Member, reveal_identifiers: bool) -> str | None:
    if not member.pan_ciphertext:
        return None
    try:
        plain = reveal(member.pan_ciphertext)
    except IdentifierError as exc:
        raise ServiceError(exc.message, exc.status_code) from exc
    return plain if reveal_identifiers else mask_pan(plain)


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.01")), "f")


def _display_aadhaar(member: Member, reveal_identifiers: bool) -> str | None:
    if not member.aadhaar_ciphertext or not reveal_identifiers:
        return None
    try:
        return mask_aadhaar(reveal(member.aadhaar_ciphertext))
    except IdentifierError as exc:
        raise ServiceError(exc.message, exc.status_code) from exc


def _flush_member(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        message = str(exc.orig).casefold()
        if "pan_lookup" in message:
            raise ServiceError("A member with this PAN already exists", 409) from exc
        if "aadhaar_lookup" in message:
            raise ServiceError("A member with this Aadhaar already exists", 409) from exc
        if "email" in message:
            raise ServiceError("A member with this email already exists", 409) from exc
        if "mobile" in message:
            raise ServiceError("Mobile number must be 10 digits") from exc
        if "last_working" in message:
            raise ServiceError("Last working date cannot be before the joining date") from exc
        raise ServiceError("The member could not be saved", 409) from exc


def _active_department_id(db: Session, department_id: int) -> int:
    department = db.get(Department, department_id)
    if department is None:
        raise ServiceError("Department not found", 404)
    if not department.is_active:
        raise ServiceError("Department is not active")
    return department.id


def _clean_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ServiceError("Full name is required")
    if len(cleaned) > 200:
        raise ServiceError("Full name is too long")
    return cleaned


def _clean_email(value: str) -> str:
    cleaned = value.strip().casefold()
    if not EMAIL_PATTERN.fullmatch(cleaned) or len(cleaned) > 254:
        raise ServiceError("Email address is invalid")
    return cleaned


def _clean_member_type(value: str) -> str:
    cleaned = value.strip().casefold()
    if cleaned not in MEMBER_TYPES:
        raise ServiceError("Member type must be employee, contract, or trainee")
    return cleaned


def _clean_mobile(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned == "":
        return None
    if len(cleaned) != 10 or not cleaned.isdigit():
        raise ServiceError("Mobile number must be 10 digits")
    return cleaned


def _clean_notice(value: int | None) -> int | None:
    if value is None:
        return None
    if value < 0:
        raise ServiceError("Notice period cannot be negative")
    return value


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _check_dates(joining_date: date, last_working_date: date | None) -> None:
    if last_working_date is not None and last_working_date < joining_date:
        raise ServiceError("Last working date cannot be before the joining date")
