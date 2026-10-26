"""Draft payroll entries and backend validation. Temporary databases only."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import AuditRecord, Department, DepartmentHeadAssignment, Member, PayrollEntry, User
from app.main import create_app
from app.services.payroll_entries import PARTIAL_MONTH, PAY_TERM_CHANGES

PASSWORD = "correct-horse"


@pytest.fixture
def payroll_db(tmp_path):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'payroll.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    department = session.scalars(select(Department).where(Department.name == "Development")).one()
    hashed = hash_password(PASSWORD)
    head = Member(
        full_name="Head Person",
        email="head@example.test",
        department_id=department.id,
        member_type="employee",
        joining_date=date(2024, 1, 1),
    )
    session.add(head)
    session.flush()
    session.add(
        DepartmentHeadAssignment(
            department_id=department.id, member_id=head.id, start_date=date(2024, 1, 1)
        )
    )
    session.add_all(
        [
            User(email="hr@example.test", password_hash=hashed, role="hr"),
            User(email="finance@example.test", password_hash=hashed, role="finance"),
            User(
                email="dh@example.test",
                password_hash=hashed,
                role="department_head",
                member_id=head.id,
            ),
        ]
    )
    session.commit()
    ids = {"department": department.id}
    session.close()
    assert DEFAULT_DATABASE_PATH.exists() == existed
    return url, ids


def _client(url):
    return TestClient(create_app(database_url=url))


def _login(client, email="hr@example.test"):
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _create_member(client, csrf, department_id, **overrides):
    body = {
        "full_name": "Asha Verma",
        "email": "asha@example.test",
        "department_id": department_id,
        "member_type": "contract",
        "joining_date": "2024-01-01",
        "pay_term": {
            "effective_from": "2024-01-01",
            "pay_basis": "monthly",
            "monthly_payment": "60000.00",
            "default_project_allowance": "5000.00",
            "default_project_bonus": "0.00",
        },
    }
    body.update(overrides)
    response = client.post("/members", json=body, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    return response.json()["member"]


def test_hr_saves_a_validated_draft_and_rejects_bad_inputs(payroll_db):
    url, ids = payroll_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    created = client.post(
        "/payroll-entries",
        json={
            "member_id": member["id"],
            "year": 2026,
            "month": 9,
            "total_leaves": "3",
            "extra_work_days": "2",
            "comp_off": "2",
            "overtime_hours": "1.50",
            "project_allowance_override": "0.00",
            "one_off_bonus": "1000.00",
            "comments": "Reviewed draft",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 200
    entry = created.json()["payroll_entry"]
    assert entry["status"] == "draft"
    assert entry["pay_basis"] == "monthly"
    assert entry["total_leaves"] == "3.0"
    assert entry["project_allowance_override"] == "0.00"
    assert entry["project_bonus_override"] is None
    assert entry["one_off_bonus"] == "1000.00"
    assert entry["one_off_bonus_authorized_by_user_id"] is not None
    assert entry["lock_version"] == 1
    assert "63000" not in created.text

    duplicate = client.post(
        "/payroll-entries",
        json={"member_id": member["id"], "year": 2026, "month": 9},
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate.status_code == 409

    half_day = client.post(
        "/payroll-entries",
        json={
            "member_id": member["id"],
            "year": 2026,
            "month": 10,
            "total_leaves": "1.3",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert half_day.status_code == 400
    assert "0.5" in half_day.json()["detail"]

    session = create_session_factory(url)()
    stored = session.scalars(select(PayrollEntry)).one()
    assert stored.project_allowance_override == Decimal("0.00")
    assert stored.project_bonus_override is None
    audits = list(session.scalars(select(AuditRecord)))
    session.close()
    assert any(row.action == "payroll.created" for row in audits)
    assert all("1000" not in (row.metadata_json or "") for row in audits)
    assert all("Reviewed" not in (row.metadata_json or "") for row in audits)


def test_partial_months_and_mid_month_pay_terms_are_blocked(payroll_db):
    url, ids = payroll_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"], joining_date="2026-09-02")
    partial = client.post(
        "/payroll-entries",
        json={"member_id": member["id"], "year": 2026, "month": 9, "total_leaves": "0"},
        headers={"X-CSRF-Token": csrf},
    )
    assert partial.status_code == 400
    assert partial.json()["detail"] == PARTIAL_MONTH

    absent = client.post(
        "/payroll-entries",
        json={"member_id": member["id"], "year": 2026, "month": 8},
        headers={"X-CSRF-Token": csrf},
    )
    assert absent.status_code == 400
    assert absent.json()["detail"] == "The member was not employed for this month"

    full_member = _create_member(
        client,
        csrf,
        ids["department"],
        full_name="Rakesh Kumar",
        email="rakesh@example.test",
    )
    changed = client.post(
        f"/members/{full_member['id']}/pay-terms",
        json={
            "effective_from": "2026-09-15",
            "pay_basis": "monthly",
            "monthly_payment": "70000.00",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert changed.status_code == 200
    blocked = client.post(
        "/payroll-entries",
        json={"member_id": full_member["id"], "year": 2026, "month": 9},
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked.status_code == 400
    assert blocked.json()["detail"] == PAY_TERM_CHANGES
    later = client.post(
        "/payroll-entries",
        json={"member_id": full_member["id"], "year": 2026, "month": 10},
        headers={"X-CSRF-Token": csrf},
    )
    assert later.status_code == 200


def test_permissions_draft_edits_and_hourly_rules(payroll_db):
    url, ids = payroll_db
    hr = _client(url)
    csrf = _login(hr)
    member = _create_member(hr, csrf, ids["department"])
    hourly = _create_member(
        hr,
        csrf,
        ids["department"],
        full_name="Hourly Worker",
        email="hourly@example.test",
        member_type="employee",
        pay_term={
            "effective_from": "2024-01-01",
            "pay_basis": "hourly",
            "hourly_rate": "500.00",
        },
    )
    session = create_session_factory(url)()
    session.add(
        User(
            email="member@example.test",
            password_hash=hash_password(PASSWORD),
            role="member",
            member_id=member["id"],
        )
    )
    session.commit()
    session.close()

    owned = hr.post(
        "/payroll-entries",
        json={"member_id": member["id"], "year": 2026, "month": 9, "total_leaves": "1.5"},
        headers={"X-CSRF-Token": csrf},
    )
    assert owned.status_code == 200
    entry_id = owned.json()["payroll_entry"]["id"]

    leaves = hr.post(
        "/payroll-entries",
        json={
            "member_id": hourly["id"],
            "year": 2026,
            "month": 9,
            "hours_worked": "80.00",
            "total_leaves": "1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert leaves.status_code == 400
    hourly_entry = hr.post(
        "/payroll-entries",
        json={"member_id": hourly["id"], "year": 2026, "month": 9, "hours_worked": "80.00"},
        headers={"X-CSRF-Token": csrf},
    )
    assert hourly_entry.status_code == 200
    assert hourly_entry.json()["payroll_entry"]["hours_worked"] == "80.00"
    assert hourly_entry.json()["payroll_entry"]["total_leaves"] == "0.0"

    assert hr.post("/payroll-entries", json={"member_id": member["id"], "year": 2026, "month": 11}).status_code == 403

    member_client = _client(url)
    member_csrf = _login(member_client, "member@example.test")
    edited = member_client.patch(
        f"/payroll-entries/{entry_id}",
        json={"lock_version": 1, "total_leaves": "2"},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert edited.status_code == 200
    assert edited.json()["payroll_entry"]["total_leaves"] == "2.0"
    assert edited.json()["payroll_entry"]["lock_version"] == 2
    bonus = member_client.patch(
        f"/payroll-entries/{entry_id}",
        json={"lock_version": 2, "one_off_bonus": "500.00"},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert bonus.status_code == 400
    stale = member_client.patch(
        f"/payroll-entries/{entry_id}",
        json={"lock_version": 1, "total_leaves": "0"},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert stale.status_code == 409

    dh = _client(url)
    dh_csrf = _login(dh, "dh@example.test")
    assert dh.get(f"/payroll-entries/{entry_id}").status_code == 200
    assert (
        dh.post(
            "/payroll-entries",
            json={"member_id": member["id"], "year": 2026, "month": 11},
            headers={"X-CSRF-Token": dh_csrf},
        ).status_code
        == 403
    )

    finance = _client(url)
    _login(finance, "finance@example.test")
    assert finance.get("/payroll-entries").status_code == 403

    session = create_session_factory(url)()
    stored = session.get(PayrollEntry, entry_id)
    stored.status = "submitted"
    session.commit()
    session.close()
    locked = hr.patch(
        f"/payroll-entries/{entry_id}",
        json={"lock_version": 2, "total_leaves": "0"},
        headers={"X-CSRF-Token": csrf},
    )
    assert locked.status_code == 409
    assert locked.json()["detail"] == "Only draft payroll entries can be edited"

    assert _client(url).get("/payroll-entries").status_code == 401
