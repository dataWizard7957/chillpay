"""Department management. Temporary databases only."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import AuditRecord, Department, DepartmentHeadAssignment, Member, User
from app.main import create_app
from app.services.department import INITIAL_DEPARTMENTS

PASSWORD = "correct-horse"


@pytest.fixture
def org_db(tmp_path):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'departments.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    hr_dept = session.scalars(select(Department).where(Department.name == "HR")).one()
    admin_dept = session.scalars(select(Department).where(Department.name == "Admin")).one()
    hashed = hash_password(PASSWORD)
    head = Member(
        full_name="Head Person",
        email="head@example.test",
        department_id=hr_dept.id,
        member_type="employee",
        joining_date=date(2024, 1, 1),
    )
    worker = Member(
        full_name="Asha Verma",
        email="asha@example.test",
        department_id=hr_dept.id,
        member_type="contract",
        joining_date=date(2024, 1, 1),
    )
    session.add_all([head, worker])
    session.flush()
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
            User(
                email="member@example.test",
                password_hash=hashed,
                role="member",
                member_id=worker.id,
            ),
        ]
    )
    session.add(
        DepartmentHeadAssignment(
            department_id=hr_dept.id, member_id=head.id, start_date=date(2026, 1, 1)
        )
    )
    session.commit()
    ids = {
        "hr_dept": hr_dept.id,
        "admin_dept": admin_dept.id,
        "head": head.id,
        "worker": worker.id,
    }
    session.close()
    assert DEFAULT_DATABASE_PATH.exists() == existed
    return url, ids


def _client(url):
    return TestClient(create_app(database_url=url))


def _login(client, email="hr@example.test"):
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_upgrade_includes_the_specified_departments(org_db):
    url, _ids = org_db
    session = create_session_factory(url)()
    names = {row.name for row in session.scalars(select(Department))}
    session.close()
    assert names == set(INITIAL_DEPARTMENTS)


def test_unauthenticated_department_access_is_rejected(org_db):
    client = _client(org_db[0])
    assert client.get("/departments").status_code == 401


def test_hr_can_create_and_rename_a_department(org_db):
    url, _ids = org_db
    client = _client(url)
    csrf = _login(client)

    created = client.post(
        "/departments",
        json={"name": "  Quality  ", "is_active": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 200
    body = created.json()["department"]
    assert body["name"] == "Quality"
    assert body["is_active"] is True
    assert body["current_head"] is None

    renamed = client.patch(
        f"/departments/{body['id']}",
        json={"name": "Quality Assurance"},
        headers={"X-CSRF-Token": csrf},
    )
    assert renamed.status_code == 200
    assert renamed.json()["department"]["name"] == "Quality Assurance"

    session = create_session_factory(url)()
    actions = [row.action for row in session.scalars(select(AuditRecord))]
    session.close()
    assert "department.created" in actions
    assert "department.updated" in actions


def test_duplicate_and_blank_department_names_are_rejected(org_db):
    client = _client(org_db[0])
    csrf = _login(client)
    duplicate = client.post(
        "/departments", json={"name": "HR"}, headers={"X-CSRF-Token": csrf}
    )
    assert duplicate.status_code == 409
    blank = client.post(
        "/departments", json={"name": "   "}, headers={"X-CSRF-Token": csrf}
    )
    assert blank.status_code == 400


def test_create_department_requires_csrf_and_hr(org_db):
    url, _ids = org_db
    hr = _client(url)
    csrf = _login(hr)
    missing = hr.post("/departments", json={"name": "Legal"})
    assert missing.status_code == 403
    assert missing.json()["detail"] == "CSRF token was missing or invalid"

    finance = _client(url)
    token = _login(finance, "finance@example.test")
    response = finance.post(
        "/departments", json={"name": "Legal"}, headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 403


def test_visibility_is_limited_by_role(org_db):
    url, ids = org_db
    finance = _client(url)
    _login(finance, "finance@example.test")
    names = {row["name"] for row in finance.get("/departments").json()["departments"]}
    assert names == set(INITIAL_DEPARTMENTS)

    member = _client(url)
    _login(member, "member@example.test")
    member_rows = member.get("/departments").json()["departments"]
    assert [row["name"] for row in member_rows] == ["HR"]
    assert member.get(f"/departments/{ids['admin_dept']}").status_code == 403

    dh = _client(url)
    _login(dh, "dh@example.test")
    dh_rows = dh.get("/departments").json()["departments"]
    assert [row["name"] for row in dh_rows] == ["HR"]
    assert dh.get(f"/departments/{ids['admin_dept']}").status_code == 403


def test_head_assignment_is_dated_and_replaced(org_db):
    url, ids = org_db
    session = create_session_factory(url)()
    other = Member(
        full_name="Second Head",
        email="second@example.test",
        department_id=ids["hr_dept"],
        member_type="employee",
        joining_date=date(2024, 1, 1),
    )
    session.add(other)
    session.commit()
    other_id = other.id
    session.close()

    client = _client(url)
    csrf = _login(client)
    replacement = client.post(
        f"/departments/{ids['hr_dept']}/heads",
        json={"member_id": other_id, "start_date": "2026-03-01"},
        headers={"X-CSRF-Token": csrf},
    )
    assert replacement.status_code == 200
    assert replacement.json()["assignment"]["member_id"] == other_id
    assert replacement.json()["assignment"]["end_date"] is None

    history = client.get(f"/departments/{ids['hr_dept']}/heads").json()["assignments"]
    assert len(history) == 2
    closed = next(row for row in history if row["member_id"] == ids["head"])
    assert closed["end_date"] == "2026-02-28"

    detail = client.get(f"/departments/{ids['hr_dept']}").json()["department"]
    assert detail["current_head"]["member_id"] == other_id

    closed_now = client.post(
        f"/departments/{ids['hr_dept']}/heads/close",
        json={"end_date": "2026-06-30"},
        headers={"X-CSRF-Token": csrf},
    )
    assert closed_now.status_code == 200
    assert closed_now.json()["assignment"]["end_date"] == "2026-06-30"
    assert client.get(f"/departments/{ids['hr_dept']}").json()["department"]["current_head"] is None


def test_inactive_department_cannot_receive_a_head(org_db):
    url, ids = org_db
    client = _client(url)
    csrf = _login(client)
    client.patch(
        f"/departments/{ids['admin_dept']}",
        json={"is_active": False},
        headers={"X-CSRF-Token": csrf},
    )
    response = client.post(
        f"/departments/{ids['admin_dept']}/heads",
        json={"member_id": ids["worker"], "start_date": "2026-10-01"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400
