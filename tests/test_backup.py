"""Backup copies the database consistently. Restore refuses the live path."""

from pathlib import Path

from app.config import DEFAULT_DATABASE_PATH
from app.db.database import upgrade_database
from app.services.backup import create_backup, restore_backup
from app.services.errors import ServiceError
import pytest


def test_backup_and_restore_use_a_separate_location(tmp_path, monkeypatch):
    existed = DEFAULT_DATABASE_PATH.exists()
    source = tmp_path / "live.sqlite3"
    invoices = tmp_path / "invoices"
    invoices.mkdir()
    (invoices / "sample.pdf").write_bytes(b"%PDF-1.4 sample")
    url = f"sqlite:///{source.as_posix()}"
    upgrade_database(url)
    monkeypatch.setenv("CHILLPAY_DATABASE_URL", url)
    monkeypatch.setenv("CHILLPAY_INVOICE_DIR", str(invoices))
    folder = create_backup(tmp_path / "backup")
    assert (folder / "chill_pay.sqlite3").is_file()
    assert (folder / "invoices" / "sample.pdf").read_bytes() == b"%PDF-1.4 sample"
    assert (folder / "manifest.json").is_file()
    restored_db = tmp_path / "restored" / "copy.sqlite3"
    restored_invoices = tmp_path / "restored" / "invoices"
    restore_backup(folder, restored_db, restored_invoices)
    assert restored_db.is_file()
    assert restored_db.stat().st_size == (folder / "chill_pay.sqlite3").stat().st_size
    assert (restored_invoices / "sample.pdf").read_bytes() == b"%PDF-1.4 sample"
    with pytest.raises(ServiceError, match="live database"):
        restore_backup(folder, DEFAULT_DATABASE_PATH, tmp_path / "nope")
    assert DEFAULT_DATABASE_PATH.exists() == existed
