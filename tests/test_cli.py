"""Initial administrator setup. Temporary databases only."""

from io import StringIO

import pytest
from sqlalchemy import select

from app.auth.passwords import verify_password
from app.cli import MIN_PASSWORD_LENGTH, create_admin, main
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import User


def _temp_url(tmp_path, monkeypatch):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'cli.sqlite3').as_posix()}"
    upgrade_database(url)
    monkeypatch.setenv("CHILLPAY_DATABASE_URL", url)
    assert DEFAULT_DATABASE_PATH.exists() == existed
    return url


def test_create_admin_creates_the_first_hr_user(tmp_path, monkeypatch):
    url = _temp_url(tmp_path, monkeypatch)
    passwords = iter(["secret12", "secret12"])
    monkeypatch.setattr("builtins.input", lambda prompt="": "admin@example.test")
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(passwords))
    stdout = StringIO()

    assert create_admin(stdout=stdout) == 0
    assert "admin@example.test" in stdout.getvalue()

    session = create_session_factory(url)()
    users = list(session.scalars(select(User)))
    session.close()
    assert len(users) == 1
    assert users[0].email == "admin@example.test"
    assert users[0].role == "hr"
    assert users[0].password_hash.startswith("$argon2id$")
    assert verify_password(users[0].password_hash, "secret12")


def test_create_admin_refuses_when_a_user_already_exists(tmp_path, monkeypatch):
    url = _temp_url(tmp_path, monkeypatch)
    session = create_session_factory(url)()
    session.add(User(email="hr@example.test", password_hash="not-checked", role="hr"))
    session.commit()
    session.close()

    stderr = StringIO()
    assert create_admin(stderr=stderr) == 1
    assert "already exists" in stderr.getvalue()

    session = create_session_factory(url)()
    count = len(list(session.scalars(select(User))))
    session.close()
    assert count == 1


def test_create_admin_rejects_mismatched_and_short_passwords(tmp_path, monkeypatch):
    url = _temp_url(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": "admin@example.test")

    mismatch = iter(["secret12", "different"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(mismatch))
    stderr = StringIO()
    assert create_admin(stderr=stderr) == 1
    assert "do not match" in stderr.getvalue()

    short = iter(["short", "short"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(short))
    stderr = StringIO()
    assert create_admin(stderr=stderr) == 1
    assert str(MIN_PASSWORD_LENGTH) in stderr.getvalue()

    session = create_session_factory(url)()
    assert list(session.scalars(select(User))) == []
    session.close()


def test_main_requires_a_command():
    with pytest.raises(SystemExit):
        main([])
