"""HR user management. Temporary databases only."""

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import Department, Member, User
from app.main import create_app

PASSWORD = "correct-horse"


def _db(tmp_path):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'users.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    department = session.scalars(select(Department).where(Department.name == "Development")).one()
    member = Member(
        full_name="Alex Member",
        email="alex@example.test",
        department_id=department.id,
        member_type="contract",
        joining_date=date(2024, 1, 1),
    )
    session.add(member)
    session.flush()
    session.add(User(email="hr@example.test", password_hash=hash_password(PASSWORD), role="hr"))
    session.commit()
    member_id = member.id
    session.close()
    assert DEFAULT_DATABASE_PATH.exists() == existed
    return url, member_id, existed


def test_hr_creates_linked_member_login_and_finance_cannot(tmp_path):
    url, member_id, existed = _db(tmp_path)
    client = TestClient(create_app(database_url=url))
    login = client.post("/auth/login", json={"email": "hr@example.test", "password": PASSWORD})
    csrf = login.json()["csrf_token"]
    missing = client.post(
        "/users",
        json={"email": "mem@example.test", "password": "longenough", "role": "member"},
        headers={"X-CSRF-Token": csrf},
    )
    assert missing.status_code == 400
    created = client.post(
        "/users",
        json={
            "email": "mem@example.test",
            "password": "longenough",
            "role": "member",
            "member_id": member_id,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 200
    assert created.json()["user"]["member_id"] == member_id
    last = client.patch(
        "/users/1",
        json={"is_active": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert last.status_code == 409
    session = create_session_factory(url)()
    session.add(User(email="finance@example.test", password_hash=hash_password(PASSWORD), role="finance"))
    session.commit()
    session.close()
    finance = TestClient(create_app(database_url=url))
    token = finance.post("/auth/login", json={"email": "finance@example.test", "password": PASSWORD}).json()[
        "csrf_token"
    ]
    denied = finance.get("/users")
    assert denied.status_code == 403
    create_denied = finance.post(
        "/users",
        json={"email": "x@example.test", "password": "longenough", "role": "hr"},
        headers={"X-CSRF-Token": token},
    )
    assert create_denied.status_code == 403
    assert DEFAULT_DATABASE_PATH.exists() == existed
