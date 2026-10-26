"""Core schema: users, sessions, members, payroll, invoices, advances, audit.

Money, day, and hour columns are integers holding ScaledDecimal values
(money and hours x100, days x10).

SQLite triggers enforce rules that must hold even if application code is bypassed:
append-only history tables, the advance recovery cap, invoices only for
approved entries, and immutable invoice versions apart from superseding.

Revision ID: 0002_core_schema
Revises: 0001_baseline
Create Date: 2026-10-16
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0002_core_schema"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

APPEND_ONLY_TABLES = ("audit_records", "approval_events", "payroll_snapshots")

SEED_SETTINGS = {
    "company_address": "Mumbai",
    "contract_tds_rate": "0.10",
    "overtime_multiplier": "1.5",
    "aadhaar_storage_enabled": "false",
}

TRIGGERS = {
    "trg_advance_recoveries_cap_insert": """
        CREATE TRIGGER trg_advance_recoveries_cap_insert
        BEFORE INSERT ON advance_recoveries
        WHEN (
            SELECT COALESCE(SUM(recovered_amount), 0) FROM advance_recoveries
            WHERE advance_id = NEW.advance_id
        ) + NEW.recovered_amount > (
            SELECT original_amount FROM advances WHERE id = NEW.advance_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'advance recovery exceeds the original advance amount');
        END
    """,
    "trg_advance_recoveries_cap_update": """
        CREATE TRIGGER trg_advance_recoveries_cap_update
        BEFORE UPDATE OF advance_id, recovered_amount ON advance_recoveries
        WHEN (
            SELECT COALESCE(SUM(recovered_amount), 0) FROM advance_recoveries
            WHERE advance_id = NEW.advance_id AND id != OLD.id
        ) + NEW.recovered_amount > (
            SELECT original_amount FROM advances WHERE id = NEW.advance_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'advance recovery exceeds the original advance amount');
        END
    """,
    "trg_invoices_require_approved_entry": """
        CREATE TRIGGER trg_invoices_require_approved_entry
        BEFORE INSERT ON invoices
        WHEN COALESCE(
            (SELECT status FROM payroll_entries WHERE id = NEW.payroll_entry_id), ''
        ) NOT IN ('approved', 'invoiced')
        BEGIN
            SELECT RAISE(ABORT, 'invoices require an approved payroll entry');
        END
    """,
    "trg_invoices_match_entry": """
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
    """,
    "trg_invoices_no_update": """
        CREATE TRIGGER trg_invoices_no_update
        BEFORE UPDATE ON invoices
        BEGIN
            SELECT RAISE(ABORT, 'invoices are immutable');
        END
    """,
    "trg_invoices_no_delete": """
        CREATE TRIGGER trg_invoices_no_delete
        BEFORE DELETE ON invoices
        BEGIN
            SELECT RAISE(ABORT, 'invoices cannot be deleted');
        END
    """,
    "trg_invoice_versions_approved_snapshot": """
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
    """,
    "trg_invoice_versions_supersede_only": """
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
    """,
    "trg_invoice_versions_no_delete": """
        CREATE TRIGGER trg_invoice_versions_no_delete
        BEFORE DELETE ON invoice_versions
        BEGIN
            SELECT RAISE(ABORT, 'invoice versions cannot be deleted');
        END
    """,
}

for _table in APPEND_ONLY_TABLES:
    TRIGGERS[f"trg_{_table}_no_update"] = f"""
        CREATE TRIGGER trg_{_table}_no_update
        BEFORE UPDATE ON {_table}
        BEGIN
            SELECT RAISE(ABORT, '{_table} is append-only');
        END
    """
    TRIGGERS[f"trg_{_table}_no_delete"] = f"""
        CREATE TRIGGER trg_{_table}_no_delete
        BEFORE DELETE ON {_table}
        BEGIN
            SELECT RAISE(ABORT, '{_table} is append-only');
        END
    """


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
        sa.UniqueConstraint("name", name=op.f("uq_departments_name")),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'inactive')", name=op.f("ck_projects_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("name", name=op.f("uq_projects_name")),
    )
    op.create_table(
        "members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("pan_ciphertext", sa.Text(), nullable=True),
        sa.Column("pan_lookup", sa.String(length=64), nullable=True),
        sa.Column("aadhaar_ciphertext", sa.Text(), nullable=True),
        sa.Column("aadhaar_lookup", sa.String(length=64), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("mobile", sa.String(length=10), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("emergency_contact", sa.Text(), nullable=True),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("member_type", sa.String(length=16), nullable=False),
        sa.Column("joining_date", sa.Date(), nullable=False),
        sa.Column("last_working_date", sa.Date(), nullable=True),
        sa.Column("notice_period_days", sa.Integer(), nullable=True),
        sa.Column("work_description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "member_type IN ('employee', 'contract', 'trainee')",
            name=op.f("ck_members_member_type"),
        ),
        sa.CheckConstraint(
            "mobile IS NULL OR (length(mobile) = 10 AND mobile NOT GLOB '*[^0-9]*')",
            name=op.f("ck_members_mobile_ten_digits"),
        ),
        sa.CheckConstraint(
            "last_working_date IS NULL OR last_working_date >= joining_date",
            name=op.f("ck_members_last_working_after_joining"),
        ),
        sa.CheckConstraint(
            "notice_period_days IS NULL OR notice_period_days >= 0",
            name=op.f("ck_members_notice_period_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name=op.f("fk_members_department_id_departments")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_members")),
        sa.UniqueConstraint("aadhaar_lookup", name=op.f("uq_members_aadhaar_lookup")),
        sa.UniqueConstraint("email", name=op.f("uq_members_email")),
        sa.UniqueConstraint("pan_lookup", name=op.f("uq_members_pan_lookup")),
    )
    op.create_index(op.f("ix_members_department_id"), "members", ["department_id"], unique=False)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "role IN ('hr', 'department_head', 'member', 'finance')", name=op.f("ck_users_role")
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name=op.f("fk_users_member_id_members")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.UniqueConstraint("member_id", name=op.f("uq_users_member_id")),
    )
    op.create_table(
        "advances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("original_amount", sa.Integer(), nullable=False),
        sa.Column("date_paid", sa.Date(), nullable=False),
        sa.Column("recovery_months", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("needs_hr_review", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'closed')", name=op.f("ck_advances_status")),
        sa.CheckConstraint(
            "original_amount > 0", name=op.f("ck_advances_original_amount_positive")
        ),
        sa.CheckConstraint("recovery_months >= 1", name=op.f("ck_advances_recovery_months")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name=op.f("fk_advances_created_by_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name=op.f("fk_advances_member_id_members")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_advances")),
    )
    op.create_index(op.f("ix_advances_member_id"), "advances", ["member_id"], unique=False)
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_app_settings_updated_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_settings")),
    )
    op.create_table(
        "audit_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_audit_records_actor_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_records")),
    )
    op.create_index(
        op.f("ix_audit_records_actor_user_id"), "audit_records", ["actor_user_id"], unique=False
    )
    op.create_index(
        op.f("ix_audit_records_created_at"), "audit_records", ["created_at"], unique=False
    )
    op.create_index(
        "ix_audit_records_target", "audit_records", ["target_type", "target_id"], unique=False
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_auth_sessions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_sessions_token_hash")),
    )
    op.create_index(op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"], unique=False)
    op.create_table(
        "department_head_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name=op.f("ck_department_head_assignments_dates"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_department_head_assignments_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_department_head_assignments_department_id_departments"),
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["members.id"],
            name=op.f("fk_department_head_assignments_member_id_members"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_department_head_assignments")),
    )
    op.create_index(
        op.f("ix_department_head_assignments_department_id"),
        "department_head_assignments",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_department_head_assignments_member_id"),
        "department_head_assignments",
        ["member_id"],
        unique=False,
    )
    op.create_index(
        "uq_department_head_assignments_one_open",
        "department_head_assignments",
        ["department_id"],
        unique=True,
        sqlite_where=sa.text("end_date IS NULL"),
    )
    op.create_table(
        "department_head_leaves",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "end_date >= start_date", name=op.f("ck_department_head_leaves_dates")
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"],
            ["users.id"],
            name=op.f("fk_department_head_leaves_cancelled_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_department_head_leaves_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_department_head_leaves_department_id_departments"),
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["members.id"],
            name=op.f("fk_department_head_leaves_member_id_members"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_department_head_leaves")),
    )
    op.create_index(
        op.f("ix_department_head_leaves_department_id"),
        "department_head_leaves",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_department_head_leaves_member_id"),
        "department_head_leaves",
        ["member_id"],
        unique=False,
    )
    op.create_table(
        "pay_terms",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("pay_basis", sa.String(length=16), nullable=False),
        sa.Column("monthly_payment", sa.Integer(), nullable=True),
        sa.Column("hourly_rate", sa.Integer(), nullable=True),
        sa.Column("default_project_allowance", sa.Integer(), nullable=False),
        sa.Column("default_project_bonus", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(pay_basis = 'monthly' AND monthly_payment IS NOT NULL AND hourly_rate IS NULL)"
            " OR (pay_basis = 'hourly' AND hourly_rate IS NOT NULL AND monthly_payment IS NULL)",
            name=op.f("ck_pay_terms_basis_matches_rate"),
        ),
        sa.CheckConstraint(
            "pay_basis IN ('monthly', 'hourly')", name=op.f("ck_pay_terms_pay_basis")
        ),
        sa.CheckConstraint(
            "(monthly_payment IS NULL OR monthly_payment >= 0)"
            " AND (hourly_rate IS NULL OR hourly_rate >= 0)"
            " AND default_project_allowance >= 0 AND default_project_bonus >= 0",
            name=op.f("ck_pay_terms_amounts_non_negative"),
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_pay_terms_dates"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name=op.f("fk_pay_terms_created_by_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name=op.f("fk_pay_terms_member_id_members")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pay_terms")),
        sa.UniqueConstraint(
            "member_id", "effective_from", name=op.f("uq_pay_terms_member_id_effective_from")
        ),
    )
    op.create_index(op.f("ix_pay_terms_member_id"), "pay_terms", ["member_id"], unique=False)
    op.create_table(
        "payroll_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("total_leaves", sa.Integer(), nullable=False),
        sa.Column("extra_work_days", sa.Integer(), nullable=False),
        sa.Column("comp_off", sa.Integer(), nullable=False),
        sa.Column("hours_worked", sa.Integer(), nullable=True),
        sa.Column("overtime_hours", sa.Integer(), nullable=False),
        sa.Column("project_allowance_override", sa.Integer(), nullable=True),
        sa.Column("project_bonus_override", sa.Integer(), nullable=True),
        sa.Column("one_off_bonus", sa.Integer(), nullable=False),
        sa.Column("one_off_bonus_authorized_by_user_id", sa.Integer(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'approved', 'rejected', 'invoiced')",
            name=op.f("ck_payroll_entries_status"),
        ),
        sa.CheckConstraint(
            "(hours_worked IS NULL OR hours_worked >= 0) AND overtime_hours >= 0",
            name=op.f("ck_payroll_entries_hours_non_negative"),
        ),
        sa.CheckConstraint(
            "(project_allowance_override IS NULL OR project_allowance_override >= 0)"
            " AND (project_bonus_override IS NULL OR project_bonus_override >= 0)"
            " AND one_off_bonus >= 0",
            name=op.f("ck_payroll_entries_amounts_non_negative"),
        ),
        sa.CheckConstraint(
            "comp_off <= total_leaves AND comp_off <= extra_work_days",
            name=op.f("ck_payroll_entries_comp_off_bounds"),
        ),
        sa.CheckConstraint(
            "extra_work_days >= 0 AND comp_off >= 0",
            name=op.f("ck_payroll_entries_days_non_negative"),
        ),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name=op.f("ck_payroll_entries_month")),
        sa.CheckConstraint(
            "one_off_bonus = 0 OR one_off_bonus_authorized_by_user_id IS NOT NULL",
            name=op.f("ck_payroll_entries_one_off_bonus_authorized"),
        ),
        sa.CheckConstraint(
            "total_leaves % 5 = 0 AND extra_work_days % 5 = 0 AND comp_off % 5 = 0",
            name=op.f("ck_payroll_entries_half_day_steps"),
        ),
        sa.CheckConstraint(
            "total_leaves BETWEEN 0 AND 300",
            name=op.f("ck_payroll_entries_total_leaves_range"),
        ),
        sa.CheckConstraint("year >= 1", name=op.f("ck_payroll_entries_year")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_payroll_entries_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name=op.f("fk_payroll_entries_member_id_members")
        ),
        sa.ForeignKeyConstraint(
            ["one_off_bonus_authorized_by_user_id"],
            ["users.id"],
            name=op.f("fk_payroll_entries_one_off_bonus_authorized_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payroll_entries")),
        sa.UniqueConstraint(
            "member_id", "year", "month", name=op.f("uq_payroll_entries_member_id_year_month")
        ),
    )
    op.create_index(
        op.f("ix_payroll_entries_member_id"), "payroll_entries", ["member_id"], unique=False
    )
    op.create_table(
        "project_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name=op.f("ck_project_assignments_dates"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_project_assignments_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name=op.f("fk_project_assignments_member_id_members")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_assignments_project_id_projects"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_assignments")),
    )
    op.create_index(
        op.f("ix_project_assignments_member_id"),
        "project_assignments",
        ["member_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_assignments_project_id"),
        "project_assignments",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "uq_project_assignments_one_open",
        "project_assignments",
        ["member_id"],
        unique=True,
        sqlite_where=sa.text("end_date IS NULL"),
    )
    op.create_table(
        "advance_installments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("advance_id", sa.Integer(), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("due_year", sa.Integer(), nullable=False),
        sa.Column("due_month", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_advance_installments_amount_positive")),
        sa.CheckConstraint(
            "due_month BETWEEN 1 AND 12", name=op.f("ck_advance_installments_due_month")
        ),
        sa.CheckConstraint(
            "sequence >= 1 AND schedule_version >= 1",
            name=op.f("ck_advance_installments_ordering"),
        ),
        sa.ForeignKeyConstraint(
            ["advance_id"],
            ["advances.id"],
            name=op.f("fk_advance_installments_advance_id_advances"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_advance_installments")),
        sa.UniqueConstraint(
            "advance_id",
            "schedule_version",
            "due_year",
            "due_month",
            name=op.f("uq_advance_installments_advance_id_schedule_version_due_year_due_month"),
        ),
        sa.UniqueConstraint(
            "advance_id",
            "schedule_version",
            "sequence",
            name=op.f("uq_advance_installments_advance_id_schedule_version_sequence"),
        ),
    )
    op.create_index(
        op.f("ix_advance_installments_advance_id"),
        "advance_installments",
        ["advance_id"],
        unique=False,
    )
    op.create_table(
        "advance_recoveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("advance_id", sa.Integer(), nullable=False),
        sa.Column("payroll_entry_id", sa.Integer(), nullable=False),
        sa.Column("scheduled_amount", sa.Integer(), nullable=False),
        sa.Column("recovered_amount", sa.Integer(), nullable=False),
        sa.Column("shortfall_amount", sa.Integer(), nullable=False),
        sa.Column("recorded_by_user_id", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "scheduled_amount >= 0 AND recovered_amount >= 0 AND shortfall_amount >= 0",
            name=op.f("ck_advance_recoveries_amounts_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["advance_id"], ["advances.id"], name=op.f("fk_advance_recoveries_advance_id_advances")
        ),
        sa.ForeignKeyConstraint(
            ["payroll_entry_id"],
            ["payroll_entries.id"],
            name=op.f("fk_advance_recoveries_payroll_entry_id_payroll_entries"),
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["users.id"],
            name=op.f("fk_advance_recoveries_recorded_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_advance_recoveries")),
        sa.UniqueConstraint(
            "advance_id",
            "payroll_entry_id",
            name=op.f("uq_advance_recoveries_advance_id_payroll_entry_id"),
        ),
    )
    op.create_index(
        op.f("ix_advance_recoveries_advance_id"), "advance_recoveries", ["advance_id"], unique=False
    )
    op.create_index(
        op.f("ix_advance_recoveries_payroll_entry_id"),
        "advance_recoveries",
        ["payroll_entry_id"],
        unique=False,
    )
    op.create_table(
        "approval_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payroll_entry_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=False),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "action IN ('submitted', 'approved', 'rejected', 'reopened')",
            name=op.f("ck_approval_events_action"),
        ),
        sa.CheckConstraint(
            "action NOT IN ('rejected', 'reopened')"
            " OR (comment IS NOT NULL AND length(trim(comment)) > 0)",
            name=op.f("ck_approval_events_comment_required"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_approval_events_actor_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["payroll_entry_id"],
            ["payroll_entries.id"],
            name=op.f("fk_approval_events_payroll_entry_id_payroll_entries"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_events")),
    )
    op.create_index(
        op.f("ix_approval_events_payroll_entry_id"),
        "approval_events",
        ["payroll_entry_id"],
        unique=False,
    )
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_number", sa.String(length=40), nullable=False),
        sa.Column("payroll_entry_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name=op.f("ck_invoices_month")),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name=op.f("fk_invoices_member_id_members")
        ),
        sa.ForeignKeyConstraint(
            ["payroll_entry_id"],
            ["payroll_entries.id"],
            name=op.f("fk_invoices_payroll_entry_id_payroll_entries"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoices")),
        sa.UniqueConstraint("invoice_number", name=op.f("uq_invoices_invoice_number")),
        sa.UniqueConstraint("payroll_entry_id", name=op.f("uq_invoices_payroll_entry_id")),
    )
    op.create_index(op.f("ix_invoices_member_id"), "invoices", ["member_id"], unique=False)
    op.create_table(
        "payroll_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payroll_entry_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("pay_term_id", sa.Integer(), nullable=False),
        sa.Column("inputs_json", sa.Text(), nullable=False),
        sa.Column("results_json", sa.Text(), nullable=False),
        sa.Column("gross_payable", sa.Integer(), nullable=False),
        sa.Column("tds", sa.Integer(), nullable=False),
        sa.Column("amount_paid", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('submitted', 'approved')", name=op.f("ck_payroll_snapshots_kind")
        ),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_payroll_snapshots_sequence")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_payroll_snapshots_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["pay_term_id"], ["pay_terms.id"], name=op.f("fk_payroll_snapshots_pay_term_id_pay_terms")
        ),
        sa.ForeignKeyConstraint(
            ["payroll_entry_id"],
            ["payroll_entries.id"],
            name=op.f("fk_payroll_snapshots_payroll_entry_id_payroll_entries"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payroll_snapshots")),
        sa.UniqueConstraint(
            "payroll_entry_id",
            "sequence",
            name=op.f("uq_payroll_snapshots_payroll_entry_id_sequence"),
        ),
    )
    op.create_index(
        op.f("ix_payroll_snapshots_payroll_entry_id"),
        "payroll_snapshots",
        ["payroll_entry_id"],
        unique=False,
    )
    op.create_table(
        "invoice_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payroll_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("generated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('current', 'superseded')", name=op.f("ck_invoice_versions_status")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_invoice_versions_version")),
        sa.ForeignKeyConstraint(
            ["generated_by_user_id"],
            ["users.id"],
            name=op.f("fk_invoice_versions_generated_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], name=op.f("fk_invoice_versions_invoice_id_invoices")
        ),
        sa.ForeignKeyConstraint(
            ["payroll_snapshot_id"],
            ["payroll_snapshots.id"],
            name=op.f("fk_invoice_versions_payroll_snapshot_id_payroll_snapshots"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_versions")),
        sa.UniqueConstraint(
            "invoice_id", "version", name=op.f("uq_invoice_versions_invoice_id_version")
        ),
    )
    op.create_index(
        op.f("ix_invoice_versions_invoice_id"), "invoice_versions", ["invoice_id"], unique=False
    )
    op.create_index(
        "uq_invoice_versions_one_current",
        "invoice_versions",
        ["invoice_id"],
        unique=True,
        sqlite_where=sa.text("status = 'current'"),
    )

    for statement in TRIGGERS.values():
        op.execute(statement)

    settings = sa.table(
        "app_settings",
        sa.column("key", sa.String),
        sa.column("value", sa.Text),
        sa.column("updated_at", sa.DateTime),
    )
    seeded_at = datetime.now(UTC).replace(tzinfo=None)
    op.bulk_insert(
        settings,
        [{"key": key, "value": value, "updated_at": seeded_at} for key, value in SEED_SETTINGS.items()],
    )


def downgrade() -> None:
    for name in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")

    op.drop_index(
        "uq_invoice_versions_one_current",
        table_name="invoice_versions",
        sqlite_where=sa.text("status = 'current'"),
    )
    op.drop_index(op.f("ix_invoice_versions_invoice_id"), table_name="invoice_versions")
    op.drop_table("invoice_versions")
    op.drop_index(op.f("ix_payroll_snapshots_payroll_entry_id"), table_name="payroll_snapshots")
    op.drop_table("payroll_snapshots")
    op.drop_index(op.f("ix_invoices_member_id"), table_name="invoices")
    op.drop_table("invoices")
    op.drop_index(op.f("ix_approval_events_payroll_entry_id"), table_name="approval_events")
    op.drop_table("approval_events")
    op.drop_index(op.f("ix_advance_recoveries_payroll_entry_id"), table_name="advance_recoveries")
    op.drop_index(op.f("ix_advance_recoveries_advance_id"), table_name="advance_recoveries")
    op.drop_table("advance_recoveries")
    op.drop_index(op.f("ix_advance_installments_advance_id"), table_name="advance_installments")
    op.drop_table("advance_installments")
    op.drop_index(
        "uq_project_assignments_one_open",
        table_name="project_assignments",
        sqlite_where=sa.text("end_date IS NULL"),
    )
    op.drop_index(op.f("ix_project_assignments_project_id"), table_name="project_assignments")
    op.drop_index(op.f("ix_project_assignments_member_id"), table_name="project_assignments")
    op.drop_table("project_assignments")
    op.drop_index(op.f("ix_payroll_entries_member_id"), table_name="payroll_entries")
    op.drop_table("payroll_entries")
    op.drop_index(op.f("ix_pay_terms_member_id"), table_name="pay_terms")
    op.drop_table("pay_terms")
    op.drop_index(
        op.f("ix_department_head_leaves_member_id"), table_name="department_head_leaves"
    )
    op.drop_index(
        op.f("ix_department_head_leaves_department_id"), table_name="department_head_leaves"
    )
    op.drop_table("department_head_leaves")
    op.drop_index(
        "uq_department_head_assignments_one_open",
        table_name="department_head_assignments",
        sqlite_where=sa.text("end_date IS NULL"),
    )
    op.drop_index(
        op.f("ix_department_head_assignments_member_id"),
        table_name="department_head_assignments",
    )
    op.drop_index(
        op.f("ix_department_head_assignments_department_id"),
        table_name="department_head_assignments",
    )
    op.drop_table("department_head_assignments")
    op.drop_index(op.f("ix_auth_sessions_user_id"), table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_audit_records_target", table_name="audit_records")
    op.drop_index(op.f("ix_audit_records_created_at"), table_name="audit_records")
    op.drop_index(op.f("ix_audit_records_actor_user_id"), table_name="audit_records")
    op.drop_table("audit_records")
    op.drop_table("app_settings")
    op.drop_index(op.f("ix_advances_member_id"), table_name="advances")
    op.drop_table("advances")
    op.drop_table("users")
    op.drop_index(op.f("ix_members_department_id"), table_name="members")
    op.drop_table("members")
    op.drop_table("projects")
    op.drop_table("departments")
