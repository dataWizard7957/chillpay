"""Request dependencies for the current user and CSRF checks."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.sessions import load_session
from app.auth.tokens import tokens_match
from app.config import CSRF_HEADER, auth_disabled, session_cookie_name
from app.db.models import User


def get_db(request: Request):
    session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    else:
        session.commit()
    finally:
        session.close()


DEFAULT_USER_EMAIL = "hr@chillpay.local"


def _default_user(db: Session) -> User:
    """With CHILLPAY_NO_AUTH, requests without a session act as one built-in HR user."""

    from secrets import token_urlsafe

    from sqlalchemy import select

    from app.services.accounts import register_company

    user = db.scalar(
        select(User).where(User.role == "hr", User.is_active.is_(True)).order_by(User.id)
    )
    if user is None:
        user = register_company(
            db,
            email=DEFAULT_USER_EMAIL,
            password=token_urlsafe(24),
            company_name="My company",
        )
    return user


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    row = load_session(db, request.cookies.get(session_cookie_name()))
    user = db.get(User, row.user_id) if row is not None else None
    if user is None or not user.is_active:
        if not auth_disabled():
            raise HTTPException(status_code=401, detail="Authentication required")
        row = None
        user = _default_user(db)
    db.info["company_id"] = user.company_id
    request.state.auth_session = row
    request.state.user = user
    return user


def require_csrf(request: Request, user: User = Depends(require_user)) -> User:
    if auth_disabled():
        return user
    supplied = request.headers.get(CSRF_HEADER)
    row = request.state.auth_session
    if supplied is None or not tokens_match(supplied, row.csrf_token_hash):
        raise HTTPException(status_code=403, detail="CSRF token was missing or invalid")
    return user


def require_roles(*roles: str):
    def dependency(user: User = Depends(require_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Not authorized")
        return user

    return dependency


def require_mutable(user: User = Depends(require_csrf)) -> User:
    if user.role == "finance":
        raise HTTPException(status_code=403, detail="Finance access is read-only")
    return user
