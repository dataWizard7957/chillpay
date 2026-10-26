"""Seed the initial departments named in the product specification.

These names are stored data, not calculation logic.

Revision ID: 0003_seed_departments
Revises: 0002_core_schema
Create Date: 2026-10-16
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0003_seed_departments"
down_revision = "0002_core_schema"
branch_labels = None
depends_on = None

INITIAL_DEPARTMENTS = ("HR", "Admin", "Digitisation", "Development")


def upgrade() -> None:
    departments = sa.table(
        "departments",
        sa.column("name", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    seeded_at = datetime.now(UTC).replace(tzinfo=None)
    op.bulk_insert(
        departments,
        [
            {
                "name": name,
                "is_active": True,
                "created_at": seeded_at,
                "updated_at": seeded_at,
            }
            for name in INITIAL_DEPARTMENTS
        ],
    )


def downgrade() -> None:
    names = ", ".join(repr(name) for name in INITIAL_DEPARTMENTS)
    op.execute(sa.text(f"DELETE FROM departments WHERE name IN ({names})"))
