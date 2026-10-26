"""Monthly payroll runs and run entries.

Revision ID: 0004_payroll_runs
Revises: 0003_seed_departments
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_payroll_runs"
down_revision = "0003_seed_departments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payroll_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("notes_text", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("year >= 1", name=op.f("ck_payroll_runs_year")),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name=op.f("ck_payroll_runs_month")),
        sa.CheckConstraint(
            "status IN ('collect', 'review', 'approve', 'invoiced')",
            name=op.f("ck_payroll_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"], ["users.id"], name=op.f("fk_payroll_runs_approved_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_payroll_runs_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payroll_runs")),
    )
    op.create_table(
        "run_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("total_leaves", sa.Integer(), nullable=False),
        sa.Column("extra_work_days", sa.Integer(), nullable=False),
        sa.Column("comp_off", sa.Integer(), nullable=False),
        sa.Column("hours_worked", sa.Integer(), nullable=False),
        sa.Column("overtime_hours", sa.Integer(), nullable=False),
        sa.Column("one_off_bonus", sa.Integer(), nullable=False),
        sa.Column("advance_amount", sa.Integer(), nullable=False),
        sa.Column("payment", sa.Integer(), nullable=True),
        sa.Column("overtime_pay", sa.Integer(), nullable=True),
        sa.Column("allowance", sa.Integer(), nullable=True),
        sa.Column("bonus", sa.Integer(), nullable=True),
        sa.Column("gross", sa.Integer(), nullable=True),
        sa.Column("tds", sa.Integer(), nullable=True),
        sa.Column("amount_paid", sa.Integer(), nullable=True),
        sa.Column("net_deposit", sa.Integer(), nullable=True),
        sa.Column("validation_errors_json", sa.Text(), nullable=False),
        sa.Column("warnings_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name=op.f("fk_run_entries_member_id_members")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["payroll_runs.id"], name=op.f("fk_run_entries_run_id_payroll_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_entries")),
        sa.UniqueConstraint("run_id", "member_id", name=op.f("uq_run_entries_run_id_member_id")),
    )
    op.create_index(op.f("ix_run_entries_run_id"), "run_entries", ["run_id"], unique=False)
    op.create_index(op.f("ix_run_entries_member_id"), "run_entries", ["member_id"], unique=False)
    for trigger in (
        "trg_invoices_require_approved_entry",
        "trg_invoices_match_entry",
        "trg_invoices_no_update",
        "trg_invoices_no_delete",
        "trg_invoice_versions_approved_snapshot",
        "trg_invoice_versions_supersede_only",
        "trg_invoice_versions_no_delete",
    ):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger}"))
    with op.batch_alter_table("invoices") as batch:
        batch.add_column(sa.Column("run_entry_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_invoices_run_entry_id_run_entries"),
            "run_entries",
            ["run_entry_id"],
            ["id"],
        )
        batch.create_unique_constraint(op.f("uq_invoices_run_entry_id"), ["run_entry_id"])
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_invoices_require_approved_entry
            BEFORE INSERT ON invoices
            WHEN COALESCE(
                (SELECT status FROM payroll_entries WHERE id = NEW.payroll_entry_id), ''
            ) NOT IN ('approved', 'invoiced')
            BEGIN
                SELECT RAISE(ABORT, 'invoices require an approved payroll entry');
            END
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_invoices_match_entry
            BEFORE INSERT ON invoices
            WHEN NOT EXISTS (
                SELECT 1 FROM payroll_entries
                WHERE id = NEW.payroll_entry_id
                  AND member_id = NEW.member_id
                  AND year = NEW.year
                  AND month = NEW.month
            )
            BEGIN
                SELECT RAISE(ABORT, 'invoice member and month must match the payroll entry');
            END
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_invoices_no_update
            BEFORE UPDATE ON invoices
            BEGIN
                SELECT RAISE(ABORT, 'invoices are immutable');
            END
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_invoices_no_delete
            BEFORE DELETE ON invoices
            BEGIN
                SELECT RAISE(ABORT, 'invoices cannot be deleted');
            END
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_invoice_versions_approved_snapshot
            BEFORE INSERT ON invoice_versions
            WHEN NOT EXISTS (
                SELECT 1 FROM payroll_snapshots AS s
                JOIN invoices AS i ON i.payroll_entry_id = s.payroll_entry_id
                WHERE s.id = NEW.payroll_snapshot_id
                  AND i.id = NEW.invoice_id
                  AND s.kind = 'approved'
            )
            BEGIN
                SELECT RAISE(ABORT, 'invoice versions require an approved snapshot of the same entry');
            END
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_invoice_versions_supersede_only
            BEFORE UPDATE ON invoice_versions
            WHEN NEW.id IS NOT OLD.id
              OR NEW.invoice_id IS NOT OLD.invoice_id
              OR NEW.version IS NOT OLD.version
              OR NEW.payroll_snapshot_id IS NOT OLD.payroll_snapshot_id
              OR NEW.file_path IS NOT OLD.file_path
              OR NEW.file_sha256 IS NOT OLD.file_sha256
              OR NEW.generated_by_user_id IS NOT OLD.generated_by_user_id
              OR NEW.generated_at IS NOT OLD.generated_at
              OR NOT (OLD.status = 'current' AND NEW.status = 'superseded')
            BEGIN
                SELECT RAISE(ABORT, 'invoice versions can only change from current to superseded');
            END
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_invoice_versions_no_delete
            BEFORE DELETE ON invoice_versions
            BEGIN
                SELECT RAISE(ABORT, 'invoice versions cannot be deleted');
            END
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("invoices") as batch:
        batch.drop_constraint(op.f("uq_invoices_run_entry_id"), type_="unique")
        batch.drop_constraint(op.f("fk_invoices_run_entry_id_run_entries"), type_="foreignkey")
        batch.drop_column("run_entry_id")
    op.drop_index(op.f("ix_run_entries_member_id"), table_name="run_entries")
    op.drop_index(op.f("ix_run_entries_run_id"), table_name="run_entries")
    op.drop_table("run_entries")
    op.drop_table("payroll_runs")
