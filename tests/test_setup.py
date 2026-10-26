"""First-time setup is open only while no users exist. Temporary databases only."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import AppSetting, User
from app.main import create_app


def _empty_client(tmp_path):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'setup.sqlite3').as_posix()}"
    upgrade_database(url)
    assert DEFAULT_DATABASE_PATH.exists() == existed
    return TestClient(create_app(database_url=url)), url, existed


def test_setup_creates_first_hr_and_then_locks(tmp_path):
    client, url, existed = _empty_client(tmp_path)
    assert client.get("/setup/status").json() == {"setup_required": True}
    response = client.post(
        "/setup",
        json={"email": "hr@example.test", "password": "longenough"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "hr"
    assert client.get("/setup/status").json() == {"setup_required": False}
    denied = client.post(
        "/setup",
        json={"email": "other@example.test", "password": "longenough"},
    )
    assert denied.status_code == 403
    session = create_session_factory(url)()
    users = list(session.scalars(select(User)))
    name = session.get(AppSetting, (1, "company_name"))
    session.close()
    assert len(users) == 1
    assert name is None
    assert DEFAULT_DATABASE_PATH.exists() == existed


def test_setup_writes_only_provided_company_fields(tmp_path):
    client, url, existed = _empty_client(tmp_path)
    client.post(
        "/setup",
        json={
            "email": "hr@example.test",
            "password": "longenough",
            "company_name": "Example Co",
            "company_address": "  ",
        },
    )
    session = create_session_factory(url)()
    assert session.get(AppSetting, (1, "company_name")).value == "Example Co"
    address = session.get(AppSetting, (1, "company_address"))
    assert address is None or address.value != ""
    phone = session.get(AppSetting, (1, "company_phone"))
    session.close()
    assert phone is None
    assert DEFAULT_DATABASE_PATH.exists() == existed
