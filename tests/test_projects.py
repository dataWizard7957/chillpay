"""Project management and assignment history. Temporary databases only."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import AuditRecord, Department, Member, ProjectAssignment, User
from app.main import create_app

PASSWORD = "correct-horse"


@pytest.fixture
def org_db(tmp_path):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'projects.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    hr_dept = session.scalars(select(Department).where(Department.name == "HR")).one()
    hashed = hash_password(PASSWORD)
    worker = Member(
        full_name="Asha Verma",
        email="asha@example.test",
        department_id=hr_dept.id,
        member_type="contract",
        joining_date=date(2024, 1, 1),
    )
    other = Member(
        full_name="Rakesh Kumar",
        email="rakesh@example.test",
        department_id=hr_dept.id,
        member_type="employee",
        joining_date=date(2024, 1, 1),
    )
    session.add_all([worker, other])
    session.flush()
    session.add_all(
        [
            User(email="hr@example.test", password_hash=hashed, role="hr"),
            User(email="finance@example.test", password_hash=hashed, role="finance"),
            User(
                email="member@example.test",
                password_hash=hashed,
                role="member",
                member_id=worker.id,
            ),
        ]
    )
    session.commit()
    ids = {"worker": worker.id, "other": other.id}
    session.close()
    assert DEFAULT_DATABASE_PATH.exists() == existed
    return url, ids


def _client(url):
    return TestClient(create_app(database_url=url))


def _login(client, email="hr@example.test"):
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_hr_creates_and_updates_a_project(org_db):
    url, _ids = org_db
    client = _client(url)
    csrf = _login(client)
    created = client.post(
        "/projects",
        json={"name": "  Alpha  ", "description": "First project", "status": "active"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 200
    project = created.json()["project"]
    assert project["name"] == "Alpha"
    assert project["description"] == "First project"
    assert project["status"] == "active"

    updated = client.patch(
        f"/projects/{project['id']}",
        json={"description": "Updated", "status": "inactive"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    assert updated.json()["project"]["status"] == "inactive"

    session = create_session_factory(url)()
    actions = [row.action for row in session.scalars(select(AuditRecord))]
    session.close()
    assert "project.created" in actions
    assert "project.updated" in actions


def test_duplicate_project_name_and_invalid_status_are_rejected(org_db):
    client = _client(org_db[0])
    csrf = _login(client)
    client.post("/projects", json={"name": "Alpha"}, headers={"X-CSRF-Token": csrf})
    duplicate = client.post(
        "/projects", json={"name": "Alpha"}, headers={"X-CSRF-Token": csrf}
    )
    assert duplicate.status_code == 409
    invalid = client.post(
        "/projects", json={"name": "Beta", "status": "archived"}, headers={"X-CSRF-Token": csrf}
    )
    assert invalid.status_code == 400


def test_finance_is_read_only_for_projects(org_db):
    url, _ids = org_db
    hr = _client(url)
    csrf = _login(hr)
    created = hr.post("/projects", json={"name": "Alpha"}, headers={"X-CSRF-Token": csrf})
    assert created.status_code == 200

    finance = _client(url)
    token = _login(finance, "finance@example.test")
    listing = finance.get("/projects")
    assert listing.status_code == 200
    assert [row["name"] for row in listing.json()["projects"]] == ["Alpha"]
    denied = finance.post(
        "/projects", json={"name": "Beta"}, headers={"X-CSRF-Token": token}
    )
    assert denied.status_code == 403


def test_member_sees_only_assigned_projects(org_db):
    url, ids = org_db
    hr = _client(url)
    csrf = _login(hr)
    alpha = hr.post("/projects", json={"name": "Alpha"}, headers={"X-CSRF-Token": csrf}).json()[
        "project"
    ]
    beta = hr.post("/projects", json={"name": "Beta"}, headers={"X-CSRF-Token": csrf}).json()[
        "project"
    ]
    assigned = hr.post(
        f"/projects/{alpha['id']}/assignments",
        json={"member_id": ids["worker"], "start_date": "2026-09-01"},
        headers={"X-CSRF-Token": csrf},
    )
    assert assigned.status_code == 200

    member = _client(url)
    _login(member, "member@example.test")
    names = [row["name"] for row in member.get("/projects").json()["projects"]]
    assert names == ["Alpha"]
    assert member.get(f"/projects/{alpha['id']}").status_code == 200
    assert member.get(f"/projects/{beta['id']}").status_code == 403


def test_reassignment_closes_the_open_project(org_db):
    url, ids = org_db
    client = _client(url)
    csrf = _login(client)
    alpha = client.post("/projects", json={"name": "Alpha"}, headers={"X-CSRF-Token": csrf}).json()[
        "project"
    ]
    beta = client.post("/projects", json={"name": "Beta"}, headers={"X-CSRF-Token": csrf}).json()[
        "project"
    ]
    first = client.post(
        f"/projects/{alpha['id']}/assignments",
        json={"member_id": ids["worker"], "start_date": "2026-09-01"},
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == 200
    second = client.post(
        f"/projects/{beta['id']}/assignments",
        json={"member_id": ids["worker"], "start_date": "2026-10-01"},
        headers={"X-CSRF-Token": csrf},
    )
    assert second.status_code == 200

    alpha_detail = client.get(f"/projects/{alpha['id']}").json()["project"]
    beta_detail = client.get(f"/projects/{beta['id']}").json()["project"]
    assert alpha_detail["assignments"][0]["end_date"] == "2026-09-30"
    assert beta_detail["assignments"][0]["end_date"] is None
    assert beta_detail["assignments"][0]["member_id"] == ids["worker"]

    closed = client.post(
        f"/projects/{beta['id']}/assignments/close",
        json={"member_id": ids["worker"], "end_date": "2026-10-15"},
        headers={"X-CSRF-Token": csrf},
    )
    assert closed.status_code == 200
    assert closed.json()["assignment"]["end_date"] == "2026-10-15"

    session = create_session_factory(url)()
    open_rows = list(
        session.scalars(select(ProjectAssignment).where(ProjectAssignment.end_date.is_(None)))
    )
    session.close()
    assert open_rows == []


def test_inactive_project_cannot_receive_assignments(org_db):
    url, ids = org_db
    client = _client(url)
    csrf = _login(client)
    project = client.post(
        "/projects",
        json={"name": "Alpha", "status": "inactive"},
        headers={"X-CSRF-Token": csrf},
    ).json()["project"]
    response = client.post(
        f"/projects/{project['id']}/assignments",
        json={"member_id": ids["worker"], "start_date": "2026-10-01"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400


def test_unauthenticated_project_access_is_rejected(org_db):
    assert _client(org_db[0]).get("/projects").status_code == 401
