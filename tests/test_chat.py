"""Chat proposes only. Temporary databases only."""

import json
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import Department, Member, PayTerm, User
from app.main import create_app

PASSWORD = "correct-horse"


def test_chat_refuses_approval_and_returns_dashboard(tmp_path, monkeypatch):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'chat.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    session.add(User(email="hr@example.test", password_hash=hash_password(PASSWORD), role="hr"))
    session.commit()
    session.close()
    client = TestClient(create_app(database_url=url))
    csrf = client.post("/auth/login", json={"email": "hr@example.test", "password": PASSWORD}).json()["csrf_token"]
    approve = client.post("/chat", json={"message": "approve this payroll"}, headers={"X-CSRF-Token": csrf})
    assert approve.status_code == 200
    assert "cannot approve" in approve.json()["chat"]["reply"].casefold()
    assert approve.json()["chat"]["proposed_action"] is None
    board = client.post("/chat", json={"message": "dashboard"}, headers={"X-CSRF-Token": csrf})
    assert board.status_code == 200
    assert "active_members" in board.json()["chat"]["dashboard"]
    assert DEFAULT_DATABASE_PATH.exists() == existed


def test_chat_extract_works_when_model_is_unavailable(tmp_path, monkeypatch):
    from app.ai.extractor import ModelUnavailableError

    def fail(_request):
        raise ModelUnavailableError("local model is unavailable")

    monkeypatch.setattr("app.services.chat.ollama_client", fail)
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'chat2.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    department = session.scalars(select(Department).where(Department.name == "Development")).one()
    session.add(
        Member(
            full_name="Alex Member",
            email="alex@example.test",
            department_id=department.id,
            member_type="contract",
            joining_date=date(2024, 1, 1),
        )
    )
    session.add(User(email="hr@example.test", password_hash=hash_password(PASSWORD), role="hr"))
    session.commit()
    session.close()
    client = TestClient(create_app(database_url=url))
    csrf = client.post("/auth/login", json={"email": "hr@example.test", "password": PASSWORD}).json()["csrf_token"]
    response = client.post(
        "/chat",
        json={"message": "Alex Member had 2 leaves"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    chat = response.json()["chat"]
    text = chat["reply"].casefold()
    assert "manual" in text or "unavailable" in text
    assert chat.get("fallback") == "manual_entry" or chat.get("model_unavailable") is True
    assert DEFAULT_DATABASE_PATH.exists() == existed


def test_chat_saves_the_note_on_the_open_monthly_run(tmp_path, monkeypatch):
    def client(_request):
        return json.dumps(
            {
                "members": [
                    {
                        "member_name": "Raj Kumar",
                        "evidence": "2 leaves",
                        "total_leaves": 2,
                        "extra_work_days": 0,
                        "comp_off": 0,
                        "hours_worked": 0,
                        "overtime_hours": 0,
                        "one_off_bonus": 0,
                        "advance_amount": 0,
                    }
                ]
            }
        )

    monkeypatch.setattr("app.services.chat.ollama_client", client)
    url = f"sqlite:///{(tmp_path / 'chat3.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    department = session.scalars(select(Department).where(Department.name == "Development")).one()
    member = Member(
        full_name="Raj Kumar",
        email="raj@example.test",
        department_id=department.id,
        member_type="employee",
        joining_date=date(2024, 1, 1),
    )
    session.add(member)
    session.flush()
    session.add(
        PayTerm(
            member_id=member.id,
            effective_from=date(2024, 1, 1),
            pay_basis="monthly",
            monthly_payment=Decimal("30000"),
            default_project_allowance=Decimal("0"),
            default_project_bonus=Decimal("0"),
        )
    )
    session.add(User(email="hr@example.test", password_hash=hash_password(PASSWORD), role="hr"))
    session.commit()
    session.close()
    client_app = TestClient(create_app(database_url=url))
    csrf = client_app.post("/auth/login", json={"email": "hr@example.test", "password": PASSWORD}).json()[
        "csrf_token"
    ]
    response = client_app.post(
        "/chat",
        json={"message": "Raj Kumar 2 leaves"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    chat = response.json()["chat"]
    assert chat["proposed_action"]["type"] == "open_monthly_run"
    assert chat["extraction"]["saved"] is True
    assert "Invoices" in chat["reply"]
    run_id = chat["proposed_action"]["run_id"]
    run = client_app.get(f"/runs/{run_id}")
    assert run.status_code == 200
    body = run.json()["run"]
    assert body["status"] == "review"
    assert body["has_notes"] is True
    assert body["entries"][0]["member_name"] == "Raj Kumar"
    assert body["entries"][0]["total_leaves"] == "2.0"
