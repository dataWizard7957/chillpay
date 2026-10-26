"""Backup API writes to a requested folder and refuses live restore."""

from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import User
from app.main import create_app

PASSWORD = "correct-horse"


def test_backup_and_restore_api_use_temporary_paths(tmp_path, monkeypatch):
    existed = DEFAULT_DATABASE_PATH.exists()
    source = tmp_path / "app.sqlite3"
    invoices = tmp_path / "invoices"
    invoices.mkdir()
    url = f"sqlite:///{source.as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    session.add(User(email="hr@example.test", password_hash=hash_password(PASSWORD), role="hr"))
    session.commit()
    session.close()
    monkeypatch.setenv("CHILLPAY_DATABASE_URL", url)
    monkeypatch.setenv("CHILLPAY_INVOICE_DIR", str(invoices))
    client = TestClient(create_app(database_url=url))
    csrf = client.post("/auth/login", json={"email": "hr@example.test", "password": PASSWORD}).json()["csrf_token"]
    dest = tmp_path / "api-backup"
    created = client.post("/backup", json={"destination": str(dest)}, headers={"X-CSRF-Token": csrf})
    assert created.status_code == 200
    folder = created.json()["backup_folder"]
    restore = client.post(
        "/backup/restore",
        json={
            "backup_folder": folder,
            "target_database": str(tmp_path / "restored.sqlite3"),
            "target_invoices": str(tmp_path / "restored-invoices"),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert restore.status_code == 200
    refused = client.post(
        "/backup/restore",
        json={
            "backup_folder": folder,
            "target_database": str(DEFAULT_DATABASE_PATH),
            "target_invoices": str(tmp_path / "nope"),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert refused.status_code == 400
    assert DEFAULT_DATABASE_PATH.exists() == existed
