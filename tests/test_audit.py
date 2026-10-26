"""Audit service and HR-only listing. Temporary databases only."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import AuditRecord, User
from app.main import create_app
from app.services.audit import AuditMetadataError, record_audit

PASSWORD = "correct-horse"


@pytest.fixture
def audit_db(tmp_path):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'audit.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    hashed = hash_password(PASSWORD)
    hr = User(email="hr@example.test", password_hash=hashed, role="hr")
    member = User(email="member@example.test", password_hash=hashed, role="member")
    finance = User(email="finance@example.test", password_hash=hashed, role="finance")
    dh = User(email="dh@example.test", password_hash=hashed, role="department_head")
    session.add_all([hr, member, finance, dh])
    session.commit()
    ids = {"hr": hr.id, "member": member.id, "finance": finance.id, "dh": dh.id}
    session.close()
    assert DEFAULT_DATABASE_PATH.exists() == existed
    return url, ids


@pytest.fixture
def client(audit_db):
    url, _ids = audit_db
    return TestClient(create_app(database_url=url))


def _login(client, email):
    return client.post("/auth/login", json={"email": email, "password": PASSWORD})


def test_record_audit_rejects_sensitive_metadata(audit_db):
    url, ids = audit_db
    session = create_session_factory(url)()
    with pytest.raises(AuditMetadataError, match="monthly_payment"):
        record_audit(
            session,
            actor_user_id=ids["hr"],
            action="pay_term.changed",
            target_type="pay_term",
            target_id="1",
            metadata={"monthly_payment": "60000"},
        )
    with pytest.raises(AuditMetadataError, match="pan"):
        record_audit(
            session,
            actor_user_id=ids["hr"],
            action="member.updated",
            target_type="member",
            metadata={"changes": {"pan": "ABCDE1234F"}},
        )
    with pytest.raises(AuditMetadataError, match="notes"):
        record_audit(
            session,
            action="advance.created",
            target_type="advance",
            metadata={"notes": "personal reason"},
        )
    with pytest.raises(AuditMetadataError, match="password"):
        record_audit(
            session,
            action="user.created",
            target_type="user",
            metadata={"password": "secret"},
        )
    session.close()


def test_record_audit_stores_safe_metadata(audit_db):
    url, ids = audit_db
    session = create_session_factory(url)()
    record = record_audit(
        session,
        actor_user_id=ids["hr"],
        action="payroll.submitted",
        target_type="payroll_entry",
        target_id="12",
        metadata={"from_status": "draft", "to_status": "submitted"},
    )
    session.commit()
    stored = session.get(AuditRecord, record.id)
    assert stored.action == "payroll.submitted"
    assert stored.target_id == "12"
    assert "draft" in stored.metadata_json
    assert "60000" not in stored.metadata_json
    session.close()


def test_get_audit_requires_an_hr_session(client, audit_db):
    url, ids = audit_db
    session = create_session_factory(url)()
    record_audit(
        session,
        actor_user_id=ids["hr"],
        action="settings.changed",
        target_type="app_setting",
        target_id="overtime_multiplier",
        metadata={"key": "overtime_multiplier"},
    )
    session.commit()
    session.close()

    assert client.get("/audit").status_code == 401

    for email in ("member@example.test", "finance@example.test", "dh@example.test"):
        other = TestClient(create_app(database_url=url))
        _login(other, email)
        response = other.get("/audit")
        assert response.status_code == 403
        assert response.json() == {"detail": "Not authorized"}

    _login(client, "hr@example.test")
    response = client.get("/audit")
    assert response.status_code == 200
    records = response.json()["records"]
    assert len(records) == 1
    assert records[0]["action"] == "settings.changed"
    assert records[0]["target_id"] == "overtime_multiplier"
    assert records[0]["actor_user_id"] == ids["hr"]


def test_audit_collection_is_not_writable(client, audit_db):
    _login(client, "hr@example.test")
    csrf = client.post(
        "/auth/login", json={"email": "hr@example.test", "password": PASSWORD}
    ).json()["csrf_token"]

    response = client.post(
        "/audit",
        json={"action": "forged"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 405

    url, _ids = audit_db
    session = create_session_factory(url)()
    assert list(session.scalars(select(AuditRecord))) == []
    session.close()
