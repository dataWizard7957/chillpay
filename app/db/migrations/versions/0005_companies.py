"""Separate each account into its own company.

Revision ID: 0005_companies
Revises: 0004_payroll_runs
Create Date: 2026-10-19
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0005_companies"
down_revision = "0004_payroll_runs"
branch_labels = None
depends_on = None

OWNED_TABLES = (
    "users",
    "departments",
    "members",
    "projects",
    "payroll_entries",
    "payroll_runs",
    "invoices",
    "advances",
    "audit_records",
)


def upgrade() -> None:
    created_at = datetime.now(UTC).replace(tzinfo=None)
    bind = op.get_bind()
    triggers = bind.execute(
        sa.text("SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND sql IS NOT NULL")
    ).fetchall()
    for name, _sql in triggers:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {name}"))

    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
    )
    companies = sa.table(
        "companies",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("created_at", sa.DateTime),
    )
    op.bulk_insert(companies, [{"id": 1, "name": "Existing company", "created_at": created_at}])

    for table in OWNED_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("company_id", sa.Integer(), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET company_id = 1"))
        with op.batch_alter_table(table) as batch:
            batch.alter_column("company_id", existing_type=sa.Integer(), nullable=False)
            batch.create_foreign_key(
                op.f(f"fk_{table}_company_id_companies"), "companies", ["company_id"], ["id"]
            )
            batch.create_index(op.f(f"ix_{table}_company_id"), ["company_id"], unique=False)

    with op.batch_alter_table("departments") as batch:
        batch.drop_constraint("uq_departments_name", type_="unique")
        batch.create_unique_constraint("uq_departments_company_id_name", ["company_id", "name"])
    with op.batch_alter_table("members") as batch:
        batch.drop_constraint("uq_members_email", type_="unique")
        batch.drop_constraint("uq_members_pan_lookup", type_="unique")
        batch.drop_constraint("uq_members_aadhaar_lookup", type_="unique")
        batch.create_unique_constraint("uq_members_company_id_email", ["company_id", "email"])
        batch.create_unique_constraint("uq_members_company_id_pan_lookup", ["company_id", "pan_lookup"])
        batch.create_unique_constraint("uq_members_company_id_aadhaar_lookup", ["company_id", "aadhaar_lookup"])
    with op.batch_alter_table("projects") as batch:
        batch.drop_constraint("uq_projects_name", type_="unique")
        batch.create_unique_constraint("uq_projects_company_id_name", ["company_id", "name"])

    op.create_table(
        "app_settings_next",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_app_settings_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_app_settings_updated_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("company_id", "key", name=op.f("pk_app_settings")),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO app_settings_next (company_id, key, value, updated_at, updated_by_user_id)
            SELECT 1, key, value, updated_at, updated_by_user_id FROM app_settings
            """
        )
    )
    op.drop_table("app_settings")
    op.rename_table("app_settings_next", "app_settings")
    for _name, sql in triggers:
        op.execute(sa.text(sql))


def downgrade() -> None:
    raise NotImplementedError
