"""Member records, PAN encryption, and pay-term history. Temporary databases only."""

from datetime import date
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import AuditRecord, Department, DepartmentHeadAssignment, Member, PayTerm, User
from app.main import create_app

PASSWORD = "correct-horse"
PAN = "ABCDE1234F"


@pytest.fixture
def member_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CHILLPAY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CHILLPAY_LOOKUP_KEY", "member-test-lookup-key")
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'members.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    hr_dept = session.scalars(select(Department).where(Department.name == "HR")).one()
    admin_dept = session.scalars(select(Department).where(Department.name == "Admin")).one()
    hashed = hash_password(PASSWORD)
    session.add_all(
        [
            User(email="hr@example.test", password_hash=hashed, role="hr"),
            User(email="finance@example.test", password_hash=hashed, role="finance"),
        ]
    )
    session.commit()
    ids = {"hr_dept": hr_dept.id, "admin_dept": admin_dept.id}
    session.close()
    assert DEFAULT_DATABASE_PATH.exists() == existed
    return url, ids


def _client(url):
    return TestClient(create_app(database_url=url))


def _login(client, email="hr@example.test"):
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _member_body(department_id, **overrides):
    body = {
        "full_name": "Asha Verma",
        "email": "asha@example.test",
        "department_id": department_id,
        "member_type": "contract",
        "joining_date": "2024-01-01",
        "pan": PAN,
        "mobile": "9876543210",
        "pay_term": {
            "effective_from": "2024-01-01",
            "pay_basis": "monthly",
            "monthly_payment": "60000.00",
            "default_project_allowance": "5000.00",
            "default_project_bonus": "0.00",
        },
    }
    body.update(overrides)
    return body


def test_hr_creates_a_member_and_pan_is_encrypted(member_db):
    url, ids = member_db
    client = _client(url)
    csrf = _login(client)
    created = client.post(
        "/members", json=_member_body(ids["hr_dept"]), headers={"X-CSRF-Token": csrf}
    )
    assert created.status_code == 200
    member = created.json()["member"]
    assert member["pan"] == PAN
    assert member["email"] == "asha@example.test"
    assert member["current_pay_term"]["monthly_payment"] == "60000.00"
    assert member["current_pay_term"]["default_project_allowance"] == "5000.00"
    assert "ciphertext" not in created.text
    assert "pan_lookup" not in created.text

    session = create_session_factory(url)()
    stored = session.scalars(select(Member)).one()
    assert stored.pan_ciphertext
    assert PAN not in stored.pan_ciphertext
    assert stored.pan_lookup != PAN
    assert len(stored.pan_lookup) == 64
    audits = list(session.scalars(select(AuditRecord)))
    session.close()
    assert any(row.action == "member.created" for row in audits)
    assert all(PAN not in (row.metadata_json or "") for row in audits)
    assert all("60000" not in (row.metadata_json or "") for row in audits)


def test_pan_is_refused_when_encryption_keys_are_missing(member_db, monkeypatch):
    monkeypatch.delenv("CHILLPAY_ENCRYPTION_KEY")
    monkeypatch.delenv("CHILLPAY_LOOKUP_KEY")
    url, ids = member_db
    client = _client(url)
    csrf = _login(client)
    response = client.post(
        "/members", json=_member_body(ids["hr_dept"]), headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Identifier encryption is not configured"
    assert PAN not in response.text

    session = create_session_factory(url)()
    assert list(session.scalars(select(Member))) == []
    session.close()


def test_invalid_pan_email_mobile_and_dates_are_rejected(member_db):
    url, ids = member_db
    client = _client(url)
    csrf = _login(client)
    invalid_pan = client.post(
        "/members",
        json=_member_body(ids["hr_dept"], pan="ABC123"),
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid_pan.status_code == 400
    bad_mobile = client.post(
        "/members",
        json=_member_body(ids["hr_dept"], pan=None, mobile="12345"),
        headers={"X-CSRF-Token": csrf},
    )
    assert bad_mobile.status_code == 400
    bad_dates = client.post(
        "/members",
        json=_member_body(ids["hr_dept"], pan=None, last_working_date="2023-01-01"),
        headers={"X-CSRF-Token": csrf},
    )
    assert bad_dates.status_code == 400


def test_duplicate_pan_and_email_are_rejected(member_db):
    url, ids = member_db
    client = _client(url)
    csrf = _login(client)
    first = client.post(
        "/members", json=_member_body(ids["hr_dept"]), headers={"X-CSRF-Token": csrf}
    )
    assert first.status_code == 200
    duplicate_pan = client.post(
        "/members",
        json=_member_body(ids["hr_dept"], email="other@example.test", pan="abcde1234f"),
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate_pan.status_code == 409
    duplicate_email = client.post(
        "/members",
        json=_member_body(ids["hr_dept"], email="ASHA@example.test", pan=None),
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate_email.status_code == 409


def test_aadhaar_storage_stays_disabled(member_db):
    url, ids = member_db
    client = _client(url)
    csrf = _login(client)
    response = client.post(
        "/members",
        json=_member_body(ids["hr_dept"], aadhaar="123412341234"),
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Aadhaar storage is disabled"


def test_pay_term_history_does_not_rewrite_earlier_amounts(member_db):
    url, ids = member_db
    client = _client(url)
    csrf = _login(client)
    created = client.post(
        "/members", json=_member_body(ids["hr_dept"]), headers={"X-CSRF-Token": csrf}
    )
    member_id = created.json()["member"]["id"]
    added = client.post(
        f"/members/{member_id}/pay-terms",
        json={
            "effective_from": "2026-06-01",
            "pay_basis": "monthly",
            "monthly_payment": "70000.00",
            "default_project_allowance": "0.00",
            "default_project_bonus": "0.00",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert added.status_code == 200
    history = client.get(f"/members/{member_id}/pay-terms").json()["pay_terms"]
    assert history[0]["monthly_payment"] == "60000.00"
    assert history[0]["default_project_allowance"] == "5000.00"
    assert history[0]["effective_to"] == "2026-05-31"
    assert history[1]["monthly_payment"] == "70000.00"
    assert history[1]["effective_to"] is None

    session = create_session_factory(url)()
    first = session.scalars(select(PayTerm).order_by(PayTerm.effective_from)).first()
    assert first.monthly_payment == Decimal("60000.00")
    audits = " ".join(row.metadata_json or "" for row in session.scalars(select(AuditRecord)))
    session.close()
    assert "70000" not in audits
    assert "60000" not in audits

    overlap = client.post(
        f"/members/{member_id}/pay-terms",
        json={
            "effective_from": "2024-01-01",
            "pay_basis": "monthly",
            "monthly_payment": "100.00",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert overlap.status_code == 409
    hourly = client.post(
        f"/members/{member_id}/pay-terms",
        json={"effective_from": "2026-07-01", "pay_basis": "hourly", "monthly_payment": "10.00"},
        headers={"X-CSRF-Token": csrf},
    )
    assert hourly.status_code == 400


def test_profile_update_does_not_change_pay_terms(member_db):
    url, ids = member_db
    client = _client(url)
    csrf = _login(client)
    member_id = client.post(
        "/members", json=_member_body(ids["hr_dept"]), headers={"X-CSRF-Token": csrf}
    ).json()["member"]["id"]
    updated = client.patch(
        f"/members/{member_id}",
        json={"full_name": "Asha V"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    assert updated.json()["member"]["full_name"] == "Asha V"
    assert updated.json()["member"]["current_pay_term"]["monthly_payment"] == "60000.00"


def test_access_is_limited_by_role(member_db):
    url, ids = member_db
    session = create_session_factory(url)()
    hashed = hash_password(PASSWORD)
    head = Member(
        full_name="Head Person",
        email="head@example.test",
        department_id=ids["hr_dept"],
        member_type="employee",
        joining_date=date(2024, 1, 1),
    )
    session.add(head)
    session.flush()
    session.add(
        DepartmentHeadAssignment(
            department_id=ids["hr_dept"], member_id=head.id, start_date=date(2026, 1, 1)
        )
    )
    session.add(
        User(
            email="dh@example.test",
            password_hash=hashed,
            role="department_head",
            member_id=head.id,
        )
    )
    session.commit()
    session.close()

    hr = _client(url)
    csrf = _login(hr)
    worker = hr.post(
        "/members", json=_member_body(ids["hr_dept"]), headers={"X-CSRF-Token": csrf}
    ).json()["member"]
    other = hr.post(
        "/members",
        json=_member_body(
            ids["admin_dept"], full_name="Rakesh Kumar", email="rakesh@example.test", pan=None
        ),
        headers={"X-CSRF-Token": csrf},
    ).json()["member"]

    session = create_session_factory(url)()
    user = session.scalars(select(User).where(User.email == "asha@example.test")).first()
    if user is None:
        session.add(
            User(
                email="member@example.test",
                password_hash=hash_password(PASSWORD),
                role="member",
                member_id=worker["id"],
            )
        )
        session.commit()
    session.close()

    dh = _client(url)
    _login(dh, "dh@example.test")
    visible = {row["email"] for row in dh.get("/members").json()["members"]}
    assert "asha@example.test" in visible
    assert "rakesh@example.test" not in visible
    masked = dh.get(f"/members/{worker['id']}").json()["member"]
    assert masked["pan"] == "XXXXXX234F"
    assert masked["address"] is None
    assert dh.get(f"/members/{other['id']}").status_code == 403

    member = _client(url)
    _login(member, "member@example.test")
    own = member.get("/members").json()["members"]
    assert [row["id"] for row in own] == [worker["id"]]
    assert own[0]["pan"] == "XXXXXX234F"
    assert member.get(f"/members/{other['id']}").status_code == 403

    finance = _client(url)
    _login(finance, "finance@example.test")
    assert finance.get("/members").status_code == 403
    assert finance.get(f"/members/{worker['id']}").status_code == 403


def test_member_writes_require_csrf_and_hr(member_db):
    url, ids = member_db
    client = _client(url)
    assert client.get("/members").status_code == 401
    csrf = _login(client)
    missing = client.post("/members", json=_member_body(ids["hr_dept"]))
    assert missing.status_code == 403

    finance = _client(url)
    token = _login(finance, "finance@example.test")
    denied = finance.post(
        "/members", json=_member_body(ids["hr_dept"]), headers={"X-CSRF-Token": token}
    )
    assert denied.status_code == 403
