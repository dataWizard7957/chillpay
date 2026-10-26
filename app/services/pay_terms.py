"""Pay-term history. A new term never rewrites an earlier term's amounts."""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import PAY_BASES, Member, PayTerm, User
from app.services.audit import record_audit
from app.services.errors import ServiceError

MONEY = Decimal("0.01")


def list_pay_terms(db: Session, member_id: int) -> list[PayTerm]:
    return list(
        db.scalars(
            select(PayTerm)
            .where(PayTerm.member_id == member_id)
            .order_by(PayTerm.effective_from, PayTerm.id)
        )
    )


def current_pay_term(db: Session, member_id: int, on_date: date) -> PayTerm | None:
    for term in reversed(list_pay_terms(db, member_id)):
        if term.effective_from <= on_date and (term.effective_to is None or term.effective_to >= on_date):
            return term
    return None


def add_pay_term(
    db: Session,
    member: Member,
    *,
    actor: User,
    effective_from: date,
    pay_basis: str,
    monthly_payment: Decimal | None,
    hourly_rate: Decimal | None,
    default_project_allowance: Decimal,
    default_project_bonus: Decimal,
) -> PayTerm:
    basis = _clean_basis(pay_basis)
    _check_basis_amounts(basis, monthly_payment, hourly_rate)
    _check_non_negative(monthly_payment, hourly_rate, default_project_allowance, default_project_bonus)

    existing = list_pay_terms(db, member.id)
    if any(term.effective_from == effective_from for term in existing):
        raise ServiceError("A pay term already starts on that date", 409)

    previous = [term for term in existing if term.effective_from < effective_from]
    following = [term for term in existing if term.effective_from > effective_from]
    next_term = following[0] if following else None
    effective_to = None if next_term is None else next_term.effective_from - timedelta(days=1)
    if effective_to is not None and effective_to < effective_from:
        raise ServiceError("The new pay term overlaps an existing term", 409)

    if previous:
        prior = previous[-1]
        if prior.effective_to is None:
            prior.effective_to = effective_from - timedelta(days=1)
            record_audit(
                db,
                actor_user_id=actor.id,
                action="pay_term.closed",
                target_type="pay_term",
                target_id=str(prior.id),
                metadata={
                    "member_id": member.id,
                    "effective_to": prior.effective_to.isoformat(),
                },
            )
        elif prior.effective_to >= effective_from:
            raise ServiceError("The new pay term overlaps an existing term", 409)

    term = PayTerm(
        member_id=member.id,
        effective_from=effective_from,
        effective_to=effective_to,
        pay_basis=basis,
        monthly_payment=monthly_payment,
        hourly_rate=hourly_rate,
        default_project_allowance=default_project_allowance,
        default_project_bonus=default_project_bonus,
        created_by_user_id=actor.id,
    )
    db.add(term)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ServiceError("The pay term could not be saved", 409) from exc
    record_audit(
        db,
        actor_user_id=actor.id,
        action="pay_term.created",
        target_type="pay_term",
        target_id=str(term.id),
        metadata={
            "member_id": member.id,
            "pay_basis": basis,
            "effective_from": effective_from.isoformat(),
        },
    )
    return term


def update_pay_term_amounts(
    db: Session,
    term: PayTerm,
    *,
    actor: User,
    monthly_payment: Decimal | None,
    hourly_rate: Decimal | None,
    default_project_allowance: Decimal,
    default_project_bonus: Decimal,
) -> PayTerm:
    """Update the amounts on the term that already starts on this date."""

    _check_basis_amounts(term.pay_basis, monthly_payment, hourly_rate)
    _check_non_negative(monthly_payment, hourly_rate, default_project_allowance, default_project_bonus)
    term.monthly_payment = monthly_payment
    term.hourly_rate = hourly_rate
    term.default_project_allowance = default_project_allowance
    term.default_project_bonus = default_project_bonus
    db.flush()
    record_audit(
        db,
        actor_user_id=actor.id,
        action="pay_term.updated",
        target_type="pay_term",
        target_id=str(term.id),
        metadata={"member_id": term.member_id, "fields": ["monthly_payment", "default_project_allowance"]},
    )
    return term


def parse_money(value: str | None, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ServiceError(f"{field_name} must be an exact decimal string")
    try:
        amount = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ServiceError(f"{field_name} is not a valid amount") from exc
    if amount != amount.quantize(MONEY):
        raise ServiceError(f"{field_name} allows at most two decimal places")
    return amount


def _clean_basis(pay_basis: str) -> str:
    cleaned = pay_basis.strip().casefold()
    if cleaned not in PAY_BASES:
        raise ServiceError("Pay basis must be monthly or hourly")
    return cleaned


def _check_basis_amounts(basis: str, monthly_payment: Decimal | None, hourly_rate: Decimal | None) -> None:
    if basis == "monthly" and (monthly_payment is None or hourly_rate is not None):
        raise ServiceError("Monthly pay requires a monthly payment and no hourly rate")
    if basis == "hourly" and (hourly_rate is None or monthly_payment is not None):
        raise ServiceError("Hourly pay requires an hourly rate and no monthly payment")


def _check_non_negative(*amounts: Decimal | None) -> None:
    for amount in amounts:
        if amount is not None and amount < 0:
            raise ServiceError("Pay amounts cannot be negative")
