"""Extraction API never saves a note. Temporary databases only."""

from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import User
from app.main import create_app


def test_extract_requires_review_and_does_not_write_members(tmp_path, monkeypatch):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'extract.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    session.add(User(email="hr@example.test", password_hash=hash_password("correct-horse"), role="hr"))
    session.commit()
    session.close()
    monkeypatch.setattr(
        "app.api.extract.ollama_client",
        lambda request: '{"member_name": "Asha Verma", "total_leaves": 3}',
    )
    client = TestClient(create_app(database_url=url))
    login = client.post("/auth/login", json={"email": "hr@example.test", "password": "correct-horse"})
    csrf = login.json()["csrf_token"]
    response = client.post("/extract", json={"note": "Asha teen din chutti"}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    body = response.json()["extraction"]
    assert body["requires_human_review"] is True
    assert body["saved"] is False
    assert body["submitted"] is False
    assert body["original_note"] == "Asha teen din chutti"
    assert client.get("/members").json()["members"] == []
    assert DEFAULT_DATABASE_PATH.exists() == existed


def test_extract_unavailable_model_keeps_manual_entry(tmp_path, monkeypatch):
    from app.ai.extractor import ModelUnavailableError

    url = f"sqlite:///{(tmp_path / 'extract2.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    session.add(User(email="hr@example.test", password_hash=hash_password("correct-horse"), role="hr"))
    session.commit()
    session.close()

    def fail(_request):
        raise ModelUnavailableError("local model is unavailable")

    monkeypatch.setattr("app.api.extract.ollama_client", fail)
    client = TestClient(create_app(database_url=url))
    csrf = client.post("/auth/login", json={"email": "hr@example.test", "password": "correct-horse"}).json()[
        "csrf_token"
    ]
    response = client.post("/extract", json={"note": "3 leaves"}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].casefold()


def test_extract_warmup_returns_without_waiting(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'extract3.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    session.add(User(email="hr@example.test", password_hash=hash_password("correct-horse"), role="hr"))
    session.commit()
    session.close()

    called = []

    def start():
        called.append(True)

    monkeypatch.setattr("app.api.extract.warmup_for_monthly_run", start)
    client = TestClient(create_app(database_url=url))
    csrf = client.post("/auth/login", json={"email": "hr@example.test", "password": "correct-horse"}).json()[
        "csrf_token"
    ]
    response = client.get("/extract/warmup", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json() == {"started": True}
    assert called == [True]
