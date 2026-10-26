"""Command-line setup. There are no default credentials."""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import func, select

from pathlib import Path

from app.config import get_database_url, load_environment
from app.db.database import create_session_factory
from app.auth.passwords import MIN_PASSWORD_LENGTH
from app.db.models import User
from app.services.backup import create_backup, restore_backup
from app.services.errors import ServiceError


def create_admin(stdout=None, stderr=None) -> int:
    """Create the first HR user. Refuses if any user already exists."""

    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    load_environment()
    factory = create_session_factory(get_database_url())
    session = factory()
    try:
        existing = session.scalar(select(func.count()).select_from(User))
        if existing:
            print("An administrator already exists. Refusing to create another.", file=stderr)
            return 1

        email = input("Admin email: ").strip()
        if not email:
            print("Email is required.", file=stderr)
            return 1

        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.", file=stderr)
            return 1
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", file=stderr)
            return 1

        from app.auth.passwords import hash_password

        session.add(User(email=email, password_hash=hash_password(password), role="hr"))
        session.commit()
        print(f"Created HR administrator {email}.", file=stdout)
        return 0
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Chill Pay setup commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create-admin", help="Create the first HR administrator")
    backup_parser = sub.add_parser("backup", help="Copy the database and invoices to a backup folder")
    backup_parser.add_argument("--output", help="Backup folder. Defaults to backups/<timestamp>.")
    restore_parser = sub.add_parser("restore", help="Restore a backup into a separate location")
    restore_parser.add_argument("--from", dest="source", required=True, help="Backup folder")
    restore_parser.add_argument("--database", required=True, help="Destination database file")
    restore_parser.add_argument("--invoices", required=True, help="Destination invoice folder")
    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return create_admin()
    if args.command == "backup":
        try:
            folder = create_backup(None if args.output is None else Path(args.output))
        except ServiceError as exc:
            print(exc.message, file=sys.stderr)
            return 1
        print(f"Backup written to {folder}")
        return 0
    if args.command == "restore":
        try:
            restore_backup(Path(args.source), Path(args.database), Path(args.invoices))
        except ServiceError as exc:
            print(exc.message, file=sys.stderr)
            return 1
        print(f"Restored database to {args.database}")
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
