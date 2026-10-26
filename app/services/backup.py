"""Consistent SQLite and invoice backups. Restore never overwrites the live file by default."""

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import DEFAULT_DATABASE_PATH, backup_directory, get_database_url, invoice_directory
from app.services.errors import ServiceError


def create_backup(destination: Path | None = None) -> Path:
    """Copy the current database with SQLite backup and include invoice files."""

    folder = destination or (backup_directory() / _stamp())
    folder.mkdir(parents=True, exist_ok=True)
    source = _database_path(get_database_url())
    if not source.is_file():
        raise ServiceError("There is no database file to back up", 404)
    target_db = folder / "chill_pay.sqlite3"
    _copy_sqlite(source, target_db)
    invoices = invoice_directory()
    invoice_copy = folder / "invoices"
    if invoices.is_dir():
        shutil.copytree(invoices, invoice_copy, dirs_exist_ok=True)
    else:
        invoice_copy.mkdir(exist_ok=True)
    manifest = {
        "created_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" "),
        "source_database": str(source),
        "invoice_count": len(list(invoice_copy.glob("*.pdf"))),
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return folder


def restore_backup(backup_folder: Path, target_database: Path, target_invoices: Path) -> None:
    """Restore a backup into a separate location. The live database path is rejected."""

    backup_db = Path(backup_folder) / "chill_pay.sqlite3"
    if not backup_db.is_file():
        raise ServiceError("The backup does not contain a database file")
    live = DEFAULT_DATABASE_PATH.resolve()
    if Path(target_database).resolve() == live:
        raise ServiceError("Restore refused: that path is the live database")
    Path(target_database).parent.mkdir(parents=True, exist_ok=True)
    _copy_sqlite(backup_db, Path(target_database))
    source_invoices = Path(backup_folder) / "invoices"
    if source_invoices.is_dir():
        if Path(target_invoices).exists():
            shutil.rmtree(target_invoices)
        shutil.copytree(source_invoices, target_invoices)


def _copy_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(str(source))
    dest_conn = sqlite3.connect(str(destination))
    try:
        source_conn.backup(dest_conn)
        dest_conn.commit()
    finally:
        dest_conn.close()
        source_conn.close()


def _database_path(url: str) -> Path:
    parsed = make_url(url)
    if parsed.database in {None, ":memory:"}:
        raise ServiceError("Only a file database can be backed up")
    return Path(parsed.database)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
