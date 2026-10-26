"""SQLite engine setup and Alembic upgrades."""

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_database_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def create_db_engine(database_url: str | None = None):
    """Create an engine. SQLite foreign keys are enabled on each connection."""

    url = database_url or get_database_url()
    _ensure_sqlite_parent(url)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


class CompanySession(Session):
    """A primary-key load from another company is treated as missing."""

    def get(self, entity, ident, **kwargs):
        obj = super().get(entity, ident, **kwargs)
        company_id = self.info.get("company_id")
        if obj is None or company_id is None:
            return obj
        owned = getattr(obj, "company_id", None)
        if owned is not None and owned != company_id:
            return None
        return obj


def create_session_factory(database_url: str | None = None):
    return sessionmaker(
        bind=create_db_engine(database_url),
        class_=CompanySession,
        autoflush=False,
        autocommit=False,
    )


def upgrade_database(database_url: str | None = None) -> None:
    """Upgrade the given database to the current Alembic head.

    The caller supplies the URL in tests so this does not write the default
    application database unless that URL is requested.
    """

    from alembic import command
    from alembic.config import Config

    url = database_url or get_database_url()
    _ensure_sqlite_parent(url)
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite:///") or database_url.endswith(":memory:"):
        return
    path_text = database_url.removeprefix("sqlite:///")
    if path_text in ("", ":memory:"):
        return
    parent = Path(path_text).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
