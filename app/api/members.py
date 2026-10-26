"""Member records and pay-term history."""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db, require_csrf, require_roles, require_user
from app.auth.policies import can_access_member_record, today
from app.db.models import PayTerm, User
from app.services.errors import ServiceError
from app.services.members import (
    create_member,
    get_member,
    list_members,
    member_view,
    pay_term_view,
    update_member,
)
from app.services.pay_terms import add_pay_term, list_pay_terms, parse_money, update_pay_term_amounts

router = APIRouter(prefix="/members", tags=["members"])


class PayTermWrite(BaseModel):
    effective_from: date
    pay_basis: str
    monthly_payment: str | None = None
    hourly_rate: str | None = None
    default_project_allowance: str = "0"
    default_project_bonus: str = "0"


class PayTermAmountWrite(BaseModel):
    monthly_payment: str | None = None
    hourly_rate: str | None = None
    default_project_allowance: str = "0"
    default_project_bonus: str = "0"


class MemberWrite(BaseModel):
    full_name: str
    email: str
    department_id: int
    member_type: str
    joining_date: date
    pay_term: PayTermWrite
    pan: str | None = None
    mobile: str | None = None
    address: str | None = None
    emergency_contact: str | None = None
    last_working_date: date | None = None
    notice_period_days: int | None = None
    work_description: str | None = None
    is_active: bool = True
    aadhaar: str | None = None


class MemberPatch(BaseModel):
    full_name: str | None = None
    email: str | None = None
    department_id: int | None = None
    member_type: str | None = None
    joining_date: date | None = None
    last_working_date: date | None = None
    notice_period_days: int | None = None
    mobile: str | None = None
    address: str | None = None
    emergency_contact: str | None = None
    work_description: str | None = None
    is_active: bool | None = None
    pan: str | None = None
    aadhaar: str | None = None


def _raise_service(exc: ServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _pay_term_fields(body: PayTermWrite) -> dict:
    return {
        "effective_from": body.effective_from,
        "pay_basis": body.pay_basis,
        "monthly_payment": parse_money(body.monthly_payment, "Monthly payment"),
        "hourly_rate": parse_money(body.hourly_rate, "Hourly rate"),
        "default_project_allowance": parse_money(
            body.default_project_allowance, "Project allowance"
        )
        or _zero(),
        "default_project_bonus": parse_money(body.default_project_bonus, "Project bonus") or _zero(),
    }


def _zero():
    from decimal import Decimal

    return Decimal("0")


def _visible_member(db: Session, user: User, member_id: int):
    try:
        member = get_member(db, member_id)
    except ServiceError as exc:
        _raise_service(exc)
    if not can_access_member_record(db, user, member):
        raise HTTPException(status_code=403, detail="Not authorized")
    return member


@router.get("")
def get_members(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    if user.role == "finance":
        raise HTTPException(status_code=403, detail="Not authorized")
    visible = [member for member in list_members(db) if can_access_member_record(db, user, member)]
    return {
        "members": [
            member_view(db, member, reveal_identifiers=user.role == "hr", on_date=today())
            for member in visible
        ]
    }


@router.get("/{member_id}")
def get_one_member(
    member_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict:
    member = _visible_member(db, user, member_id)
    return {"member": member_view(db, member, reveal_identifiers=user.role == "hr", on_date=today())}


@router.post("")
def post_member(
    body: MemberWrite,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    fields = body.model_dump(exclude={"pay_term"}, exclude_unset=True)
    try:
        member = create_member(db, user, fields, _pay_term_fields(body.pay_term))
    except ServiceError as exc:
        _raise_service(exc)
    return {"member": member_view(db, member, reveal_identifiers=True, on_date=today())}


@router.patch("/{member_id}")
def patch_member(
    member_id: int,
    body: MemberPatch,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    try:
        member = update_member(db, get_member(db, member_id), user, changes)
    except ServiceError as exc:
        _raise_service(exc)
    return {"member": member_view(db, member, reveal_identifiers=True, on_date=today())}


@router.get("/{member_id}/pay-terms")
def get_pay_terms(
    member_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict:
    member = _visible_member(db, user, member_id)
    return {"pay_terms": [pay_term_view(term) for term in list_pay_terms(db, member.id)]}


@router.post("/{member_id}/pay-terms")
def post_pay_term(
    member_id: int,
    body: PayTermWrite,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    try:
        term = add_pay_term(db, get_member(db, member_id), actor=user, **_pay_term_fields(body))
    except ServiceError as exc:
        _raise_service(exc)
    return {"pay_term": pay_term_view(term)}


@router.patch("/{member_id}/pay-terms/{term_id}")
def patch_pay_term(
    member_id: int,
    term_id: int,
    body: PayTermAmountWrite,
    user: User = Depends(require_roles("hr")),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    member = get_member(db, member_id)
    term = db.get(PayTerm, term_id)
    if term is None or term.member_id != member.id:
        raise HTTPException(status_code=404, detail="Pay term not found")
    allowance = parse_money(body.default_project_allowance, "Default project allowance")
    bonus = parse_money(body.default_project_bonus, "Default project bonus")
    try:
        updated = update_pay_term_amounts(
            db,
            term,
            actor=user,
            monthly_payment=parse_money(body.monthly_payment, "Monthly payment"),
            hourly_rate=parse_money(body.hourly_rate, "Hourly rate"),
            default_project_allowance=Decimal("0") if allowance is None else allowance,
            default_project_bonus=Decimal("0") if bonus is None else bonus,
        )
    except ServiceError as exc:
        _raise_service(exc)
    return {"pay_term": pay_term_view(updated)}
