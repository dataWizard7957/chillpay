"""Request dependencies for the current user and CSRF checks."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.sessions import load_session
from app.auth.tokens import tokens_match
from app.config import CSRF_HEADER, session_cookie_name
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


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    row = load_session(db, request.cookies.get(session_cookie_name()))
    if row is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Authentication required")
    db.info["company_id"] = user.company_id
    request.state.auth_session = row
    request.state.user = user
    return user


def require_csrf(request: Request, user: User = Depends(require_user)) -> User:
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
