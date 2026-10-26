"""Authentication, sessions, and CSRF. Temporary databases only."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import hash_token, new_token
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import AuthSession, User, utcnow
from app.main import create_app

PASSWORD = "correct-horse"
GENERIC_ERROR = {"detail": "Invalid email or password"}


@pytest.fixture
def auth_db(tmp_path):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'auth.sqlite3').as_posix()}"
    upgrade_database(url)
    factory = create_session_factory(url)
    session = factory()
    user = User(email="hr@example.test", password_hash=hash_password(PASSWORD), role="hr")
    session.add(user)
    session.commit()
    user_id = user.id
    session.close()
    assert DEFAULT_DATABASE_PATH.exists() == existed
    return url, user_id


@pytest.fixture
def client(auth_db):
    url, _user_id = auth_db
    return TestClient(create_app(database_url=url))


def _login(client, email="hr@example.test", password=PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_password_hash_is_argon2id_and_verifies():
    hashed = hash_password(PASSWORD)
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, PASSWORD) is True
    assert verify_password(hashed, "wrong-password") is False


def test_verify_password_rejects_malformed_stored_hashes():
    hashed = hash_password(PASSWORD)
    assert verify_password("not-an-argon2-hash", PASSWORD) is False
    assert verify_password("", PASSWORD) is False
    assert verify_password("$argon2id$truncated", PASSWORD) is False
    assert verify_password(hashed[:-8], PASSWORD) is False


def test_login_sets_httponly_strict_cookie_and_returns_csrf(client, auth_db):
    url, user_id = auth_db
    response = _login(client)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"csrf_token", "user"}
    assert body["user"] == {"id": user_id, "email": "hr@example.test", "role": "hr"}
    assert body["csrf_token"]
    assert "session" not in body
    assert PASSWORD not in response.text

    set_cookie = response.headers["set-cookie"]
    assert "chillpay_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=strict" in set_cookie.lower()
    assert "secure" not in set_cookie.lower()

    factory = create_session_factory(url)
    session = factory()
    rows = list(session.scalars(select(AuthSession)))
    session.close()
    raw = response.cookies["chillpay_session"]
    assert len(rows) == 1
    assert rows[0].token_hash == hash_token(raw)
    assert rows[0].csrf_token_hash == hash_token(body["csrf_token"])
    assert raw not in rows[0].token_hash
    assert body["csrf_token"] not in rows[0].csrf_token_hash


def test_login_checks_the_password_and_signup_opens_a_new_company(client, auth_db):
    url, user_id = auth_db
    wrong = _login(client, password="wrong-password")
    assert wrong.status_code == 401
    assert wrong.json() == GENERIC_ERROR

    unknown = _login(client, email="missing@example.test", password="anything-1")
    assert unknown.status_code == 401

    created = client.post(
        "/auth/signup",
        json={
            "email": "other@example.test",
            "password": "another-secret",
            "company_name": "Other Co",
        },
    )
    assert created.status_code == 200
    assert created.json()["user"]["email"] == "other@example.test"
    assert created.json()["user"]["role"] == "hr"
    assert created.json()["user"]["id"] != user_id

    factory = create_session_factory(url)
    session = factory()
    other = session.scalar(select(User).where(User.email == "other@example.test"))
    original = session.get(User, user_id)
    assert other.company_id != original.company_id
    session.close()


def test_inactive_user_cannot_log_in(auth_db):
    url, user_id = auth_db
    factory = create_session_factory(url)
    session = factory()
    user = session.get(User, user_id)
    user.is_active = False
    session.commit()
    session.close()

    response = _login(TestClient(create_app(database_url=url)))
    assert response.status_code == 401
    assert response.json() == GENERIC_ERROR


def test_me_requires_a_session_and_returns_the_user(client, auth_db):
    _url, user_id = auth_db
    assert client.get("/auth/me").status_code == 401

    login = _login(client)
    response = client.get("/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {"id": user_id, "email": "hr@example.test", "role": "hr"}
    assert body["csrf_token"]
    assert body["csrf_token"] != login.json()["csrf_token"]


def test_logout_requires_csrf_and_revokes_the_session(client):
    login = _login(client)
    csrf = login.json()["csrf_token"]

    missing = client.post("/auth/logout")
    assert missing.status_code == 403
    assert missing.json() == {"detail": "CSRF token was missing or invalid"}

    wrong = client.post("/auth/logout", headers={"X-CSRF-Token": "not-the-token"})
    assert wrong.status_code == 403

    done = client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    assert done.status_code == 200
    assert done.json() == {"status": "logged_out"}
    assert client.get("/auth/me").status_code == 401


def test_idle_session_is_rejected(auth_db):
    url, user_id = auth_db
    token = new_token()
    factory = create_session_factory(url)
    session = factory()
    session.add(
        AuthSession(
            token_hash=hash_token(token),
            csrf_token_hash=hash_token(new_token()),
            user_id=user_id,
            created_at=utcnow() - timedelta(hours=1),
            last_seen_at=utcnow() - timedelta(minutes=31),
            expires_at=utcnow() + timedelta(hours=7),
        )
    )
    session.commit()
    session.close()

    client = TestClient(create_app(database_url=url))
    client.cookies.set("chillpay_session", token)
    response = client.get("/auth/me")
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_absolute_session_expiry_is_rejected(auth_db):
    url, user_id = auth_db
    token = new_token()
    factory = create_session_factory(url)
    session = factory()
    session.add(
        AuthSession(
            token_hash=hash_token(token),
            csrf_token_hash=hash_token(new_token()),
            user_id=user_id,
            created_at=utcnow() - timedelta(hours=9),
            last_seen_at=utcnow(),
            expires_at=utcnow() - timedelta(minutes=1),
        )
    )
    session.commit()
    session.close()

    client = TestClient(create_app(database_url=url))
    client.cookies.set("chillpay_session", token)
    assert client.get("/auth/me").status_code == 401


def test_revoked_session_is_rejected(auth_db):
    url, user_id = auth_db
    token = new_token()
    factory = create_session_factory(url)
    session = factory()
    session.add(
        AuthSession(
            token_hash=hash_token(token),
            csrf_token_hash=hash_token(new_token()),
            user_id=user_id,
            expires_at=utcnow() + timedelta(hours=8),
            revoked_at=utcnow(),
        )
    )
    session.commit()
    session.close()

    client = TestClient(create_app(database_url=url))
    client.cookies.set("chillpay_session", token)
    assert client.get("/auth/me").status_code == 401


def test_secure_cookie_flag_follows_the_environment(auth_db, monkeypatch):
    monkeypatch.setenv("CHILLPAY_SESSION_SECURE", "true")
    client = TestClient(create_app(database_url=auth_db[0]))
    response = _login(client)
    assert "secure" in response.headers["set-cookie"].lower()


def test_cors_allows_credentials_for_the_local_frontend(client):
    response = client.options(
        "/auth/login",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
    assert response.headers.get("access-control-allow-credentials") == "true"

    rejected = client.options(
        "/auth/login",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert rejected.headers.get("access-control-allow-origin") != "http://evil.example"


def test_health_still_requires_no_authentication(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_app_can_use_a_temporary_database(auth_db):
    url, _user_id = auth_db
    app = create_app(database_url=url)
    assert app.state.database_url == url
