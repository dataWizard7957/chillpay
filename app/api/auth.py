"""Login, logout, and the current session."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db, require_csrf, require_user
from app.auth.passwords import dummy_password_hash, verify_password
from app.auth.sessions import create_session, revoke_session
from app.auth.tokens import hash_token, new_token
from app.config import session_absolute_hours, session_cookie_name, session_cookie_secure
from app.db.models import User, utcnow
from app.services.accounts import register_company
from app.services.errors import ServiceError

GENERIC_LOGIN_ERROR = "Invalid email or password"

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    email: str
    password: str
    company_name: str


def _user_payload(user: User) -> dict:
    return {"id": user.id, "email": user.email, "role": user.role}


def _set_session_cookie(response: Response, session_token: str) -> None:
    response.set_cookie(
        key=session_cookie_name(),
        value=session_token,
        httponly=True,
        samesite="strict",
        secure=session_cookie_secure(),
        path="/",
        max_age=session_absolute_hours() * 3600,
    )


@router.post("/signup")
def signup(body: SignupRequest, response: Response, db: Session = Depends(get_db)) -> dict:
    """Create a new company and sign in as its HR user."""

    try:
        user = register_company(
            db,
            email=body.email,
            password=body.password,
            company_name=body.company_name,
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return _start_session(response, db, user)


@router.post("/login")
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)) -> dict:
    """Sign in with the password stored for this email."""

    email = body.email.strip().casefold()
    user = None
    if email and "@" in email and body.password:
        user = db.scalar(select(User).where(User.email == email))
    password_hash = user.password_hash if user is not None else dummy_password_hash()
    if user is None or not user.is_active or not verify_password(password_hash, body.password):
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)
    return _start_session(response, db, user)


def _start_session(response: Response, db: Session, user: User) -> dict:
    session_token, csrf_token = create_session(db, user.id)
    user.last_login_at = utcnow()
    _set_session_cookie(response, session_token)
    return {"csrf_token": csrf_token, "user": _user_payload(user)}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    _user: User = Depends(require_csrf),
) -> dict:
    revoke_session(request.state.auth_session)
    response.delete_cookie(
        key=session_cookie_name(),
        path="/",
        httponly=True,
        samesite="strict",
        secure=session_cookie_secure(),
    )
    return {"status": "logged_out"}


@router.get("/me")
def me(request: Request, user: User = Depends(require_user)) -> dict:
    """Return the current user and a fresh CSRF token.

    The previous token cannot be recovered because only its hash is stored.
    """

    token = new_token()
    request.state.auth_session.csrf_token_hash = hash_token(token)
    return {"csrf_token": token, "user": _user_payload(user)}
