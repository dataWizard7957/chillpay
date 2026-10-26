"""Server-side sessions. Idle and absolute expiry are both enforced."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.tokens import hash_token, new_token
from app.config import session_absolute_hours, session_idle_minutes
from app.db.models import AuthSession, utcnow


def create_session(db: Session, user_id: int) -> tuple[str, str]:
    session_token = new_token()
    csrf_token = new_token()
    now = utcnow()
    db.add(
        AuthSession(
            token_hash=hash_token(session_token),
            csrf_token_hash=hash_token(csrf_token),
            user_id=user_id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=session_absolute_hours()),
        )
    )
    db.flush()
    return session_token, csrf_token


def load_session(db: Session, session_token: str | None) -> AuthSession | None:
    if not session_token:
        return None
    row = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(session_token))
    )
    if row is None or row.revoked_at is not None:
        return None
    now = utcnow()
    if now >= row.expires_at:
        return None
    if now - row.last_seen_at >= timedelta(minutes=session_idle_minutes()):
        return None
    row.last_seen_at = now
    db.flush()
    return row


def revoke_session(row: AuthSession) -> None:
    row.revoked_at = utcnow()
