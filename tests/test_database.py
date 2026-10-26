"""Database initialization tests. These use a temporary SQLite file only."""

from sqlalchemy import create_engine, text

from app.config import DEFAULT_DATABASE_PATH, DEFAULT_DATABASE_URL, PROJECT_ROOT
from app.db.database import create_db_engine, upgrade_database

HEAD_REVISION = "0005_companies"


def test_upgrade_head_records_core_schema_on_a_temporary_database(tmp_path):
    database_path = tmp_path / "chill_pay_test.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    default_existed_before = DEFAULT_DATABASE_PATH.exists()

    upgrade_database(database_url)
    upgrade_database(database_url)

    engine = create_engine(database_url)
    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        row_count = connection.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar_one()

    assert database_path.exists()
    assert version == HEAD_REVISION
    assert row_count == 1
    assert DEFAULT_DATABASE_PATH.exists() == default_existed_before
    engine.dispose()


def test_default_database_is_anchored_to_project_data_directory():
    assert DEFAULT_DATABASE_PATH == PROJECT_ROOT / "data" / "chill_pay.sqlite3"
    assert DEFAULT_DATABASE_PATH.is_absolute()
    assert DEFAULT_DATABASE_URL == f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"


def test_sqlite_engine_enables_foreign_keys(tmp_path):
    database_path = tmp_path / "foreign_keys.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_db_engine(database_url)

    with engine.connect() as connection:
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()

    assert foreign_keys == 1
    engine.dispose()
