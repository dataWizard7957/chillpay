"""Persistent Chill Pay records.

Money uses ScaledDecimal(2), days ScaledDecimal(1), and hours ScaledDecimal(2).
They are stored as integers, so CHECK constraints below compare scaled values:
a day limit of 30 is written as 300, and half-day steps as multiples of 5.

Timestamps are naive UTC.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, with_loader_criteria

from app.db.types import ScaledDecimal

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

MONEY = ScaledDecimal(2)
DAYS = ScaledDecimal(1)
HOURS = ScaledDecimal(2)

ROLES = ("hr", "department_head", "member", "finance")
MEMBER_TYPES = ("employee", "contract", "trainee")
PAY_BASES = ("monthly", "hourly")
PROJECT_STATUSES = ("active", "inactive")
PAYROLL_STATUSES = ("draft", "submitted", "approved", "rejected", "invoiced")
PAYROLL_RUN_STATUSES = ("collect", "review", "approve", "invoiced")
SNAPSHOT_KINDS = ("submitted", "approved")
APPROVAL_ACTIONS = ("submitted", "approved", "rejected", "reopened")
INVOICE_VERSION_STATUSES = ("current", "superseded")
ADVANCE_STATUSES = ("active", "closed")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Company(Base):
    """One payroll workspace. Accounts in other companies cannot see its rows."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_one_of("role", ROLES), name="role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuthSession(Base):
    """Server-side session. Only SHA-256 hashes of the tokens are stored."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("company_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Member(Base):
    """A person paid by the company. Pay terms live in PayTerm history."""

    __tablename__ = "members"
    __table_args__ = (
        UniqueConstraint("company_id", "email"),
        UniqueConstraint("company_id", "pan_lookup"),
        UniqueConstraint("company_id", "aadhaar_lookup"),
        CheckConstraint(_one_of("member_type", MEMBER_TYPES), name="member_type"),
        CheckConstraint(
            "mobile IS NULL OR (length(mobile) = 10 AND mobile NOT GLOB '*[^0-9]*')",
            name="mobile_ten_digits",
        ),
        CheckConstraint(
            "last_working_date IS NULL OR last_working_date >= joining_date",
            name="last_working_after_joining",
        ),
        CheckConstraint(
            "notice_period_days IS NULL OR notice_period_days >= 0",
            name="notice_period_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    pan_ciphertext: Mapped[str | None] = mapped_column(Text)
    pan_lookup: Mapped[str | None] = mapped_column(String(64))
    aadhaar_ciphertext: Mapped[str | None] = mapped_column(Text)
    aadhaar_lookup: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(Text)
    mobile: Mapped[str | None] = mapped_column(String(10))
    email: Mapped[str] = mapped_column(String(254))
    emergency_contact: Mapped[str | None] = mapped_column(Text)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    member_type: Mapped[str] = mapped_column(String(16))
    joining_date: Mapped[date] = mapped_column(Date)
    last_working_date: Mapped[date | None] = mapped_column(Date)
    notice_period_days: Mapped[int | None] = mapped_column(Integer)
    work_description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DepartmentHeadAssignment(Base):
    __tablename__ = "department_head_assignments"
    __table_args__ = (
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="dates"),
        Index(
            "uq_department_head_assignments_one_open",
            "department_id",
            unique=True,
            sqlite_where=text("end_date IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DepartmentHeadLeave(Base):
    """An explicit HR record that a Department Head is on leave. Never inferred."""

    __tablename__ = "department_head_leaves"
    __table_args__ = (CheckConstraint("end_date >= start_date", name="dates"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("company_id", "name"),
        CheckConstraint(_one_of("status", PROJECT_STATUSES), name="status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ProjectAssignment(Base):
    """Dated project history. A member has at most one open assignment."""

    __tablename__ = "project_assignments"
    __table_args__ = (
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="dates"),
        Index(
            "uq_project_assignments_one_open",
            "member_id",
            unique=True,
            sqlite_where=text("end_date IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PayTerm(Base):
    """Pay terms with an effective date. New terms never rewrite old rows."""

    __tablename__ = "pay_terms"
    __table_args__ = (
        UniqueConstraint("member_id", "effective_from"),
        CheckConstraint(_one_of("pay_basis", PAY_BASES), name="pay_basis"),
        CheckConstraint(
            "(pay_basis = 'monthly' AND monthly_payment IS NOT NULL AND hourly_rate IS NULL)"
            " OR (pay_basis = 'hourly' AND hourly_rate IS NOT NULL AND monthly_payment IS NULL)",
            name="basis_matches_rate",
        ),
        CheckConstraint(
            "(monthly_payment IS NULL OR monthly_payment >= 0)"
            " AND (hourly_rate IS NULL OR hourly_rate >= 0)"
            " AND default_project_allowance >= 0 AND default_project_bonus >= 0",
            name="amounts_non_negative",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="dates",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    pay_basis: Mapped[str] = mapped_column(String(16))
    monthly_payment: Mapped[Decimal | None] = mapped_column(MONEY)
    hourly_rate: Mapped[Decimal | None] = mapped_column(MONEY)
    default_project_allowance: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    default_project_bonus: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PayrollEntry(Base):
    """One entry per member per payroll month."""

    __tablename__ = "payroll_entries"
    __table_args__ = (
        UniqueConstraint("member_id", "year", "month"),
        CheckConstraint("year >= 1", name="year"),
        CheckConstraint("month BETWEEN 1 AND 12", name="month"),
        CheckConstraint(_one_of("status", PAYROLL_STATUSES), name="status"),
        CheckConstraint("total_leaves BETWEEN 0 AND 300", name="total_leaves_range"),
        CheckConstraint("extra_work_days >= 0 AND comp_off >= 0", name="days_non_negative"),
        CheckConstraint(
            "total_leaves % 5 = 0 AND extra_work_days % 5 = 0 AND comp_off % 5 = 0",
            name="half_day_steps",
        ),
        CheckConstraint(
            "comp_off <= total_leaves AND comp_off <= extra_work_days",
            name="comp_off_bounds",
        ),
        CheckConstraint(
            "(hours_worked IS NULL OR hours_worked >= 0) AND overtime_hours >= 0",
            name="hours_non_negative",
        ),
        CheckConstraint(
            "(project_allowance_override IS NULL OR project_allowance_override >= 0)"
            " AND (project_bonus_override IS NULL OR project_bonus_override >= 0)"
            " AND one_off_bonus >= 0",
            name="amounts_non_negative",
        ),
        CheckConstraint(
            "one_off_bonus = 0 OR one_off_bonus_authorized_by_user_id IS NOT NULL",
            name="one_off_bonus_authorized",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    total_leaves: Mapped[Decimal] = mapped_column(DAYS, default=Decimal("0"))
    extra_work_days: Mapped[Decimal] = mapped_column(DAYS, default=Decimal("0"))
    comp_off: Mapped[Decimal] = mapped_column(DAYS, default=Decimal("0"))
    hours_worked: Mapped[Decimal | None] = mapped_column(HOURS)
    overtime_hours: Mapped[Decimal] = mapped_column(HOURS, default=Decimal("0"))
    project_allowance_override: Mapped[Decimal | None] = mapped_column(MONEY)
    project_bonus_override: Mapped[Decimal | None] = mapped_column(MONEY)
    one_off_bonus: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    one_off_bonus_authorized_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id")
    )
    comments: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class PayrollSnapshot(Base):
    """Calculation inputs and results saved at submission and approval. Never updated."""

    __tablename__ = "payroll_snapshots"
    __table_args__ = (
        UniqueConstraint("payroll_entry_id", "sequence"),
        CheckConstraint(_one_of("kind", SNAPSHOT_KINDS), name="kind"),
        CheckConstraint("sequence >= 1", name="sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    payroll_entry_id: Mapped[int] = mapped_column(ForeignKey("payroll_entries.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    pay_term_id: Mapped[int] = mapped_column(ForeignKey("pay_terms.id"))
    inputs_json: Mapped[str] = mapped_column(Text)
    results_json: Mapped[str] = mapped_column(Text)
    gross_payable: Mapped[Decimal] = mapped_column(MONEY)
    tds: Mapped[Decimal] = mapped_column(MONEY)
    amount_paid: Mapped[Decimal] = mapped_column(MONEY)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApprovalEvent(Base):
    """Append-only workflow history."""

    __tablename__ = "approval_events"
    __table_args__ = (
        CheckConstraint(_one_of("action", APPROVAL_ACTIONS), name="action"),
        CheckConstraint(
            "action NOT IN ('rejected', 'reopened')"
            " OR (comment IS NOT NULL AND length(trim(comment)) > 0)",
            name="comment_required",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    payroll_entry_id: Mapped[int] = mapped_column(ForeignKey("payroll_entries.id"), index=True)
    action: Mapped[str] = mapped_column(String(16))
    from_status: Mapped[str] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PayrollRun(Base):
    """One monthly payroll run. Status moves collect → review → approve → invoiced."""

    __tablename__ = "payroll_runs"
    __table_args__ = (
        CheckConstraint("year >= 1", name="year"),
        CheckConstraint("month BETWEEN 1 AND 12", name="month"),
        CheckConstraint(_one_of("status", PAYROLL_RUN_STATUSES), name="status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="collect")
    notes_text: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RunEntry(Base):
    """One proposed payroll row per member in a monthly run."""

    __tablename__ = "run_entries"
    __table_args__ = (UniqueConstraint("run_id", "member_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("payroll_runs.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    evidence: Mapped[str] = mapped_column(Text, default="")
    total_leaves: Mapped[Decimal] = mapped_column(DAYS, default=Decimal("0"))
    extra_work_days: Mapped[Decimal] = mapped_column(DAYS, default=Decimal("0"))
    comp_off: Mapped[Decimal] = mapped_column(DAYS, default=Decimal("0"))
    hours_worked: Mapped[Decimal] = mapped_column(HOURS, default=Decimal("0"))
    overtime_hours: Mapped[Decimal] = mapped_column(HOURS, default=Decimal("0"))
    one_off_bonus: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    advance_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    payment: Mapped[Decimal | None] = mapped_column(MONEY)
    overtime_pay: Mapped[Decimal | None] = mapped_column(MONEY)
    allowance: Mapped[Decimal | None] = mapped_column(MONEY)
    bonus: Mapped[Decimal | None] = mapped_column(MONEY)
    gross: Mapped[Decimal | None] = mapped_column(MONEY)
    tds: Mapped[Decimal | None] = mapped_column(MONEY)
    amount_paid: Mapped[Decimal | None] = mapped_column(MONEY)
    net_deposit: Mapped[Decimal | None] = mapped_column(MONEY)
    validation_errors_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")


class Invoice(Base):
    """One invoice number per payroll entry. Regeneration adds versions."""

    __tablename__ = "invoices"
    __table_args__ = (CheckConstraint("month BETWEEN 1 AND 12", name="month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    invoice_number: Mapped[str] = mapped_column(String(40), unique=True)
    payroll_entry_id: Mapped[int] = mapped_column(ForeignKey("payroll_entries.id"), unique=True)
    run_entry_id: Mapped[int | None] = mapped_column(ForeignKey("run_entries.id"), unique=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InvoiceVersion(Base):
    __tablename__ = "invoice_versions"
    __table_args__ = (
        UniqueConstraint("invoice_id", "version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(_one_of("status", INVOICE_VERSION_STATUSES), name="status"),
        Index(
            "uq_invoice_versions_one_current",
            "invoice_id",
            unique=True,
            sqlite_where=text("status = 'current'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    payroll_snapshot_id: Mapped[int] = mapped_column(ForeignKey("payroll_snapshots.id"))
    status: Mapped[str] = mapped_column(String(16), default="current")
    file_path: Mapped[str] = mapped_column(Text)
    file_sha256: Mapped[str] = mapped_column(String(64))
    generated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Advance(Base):
    """Advance ledger. Recovered and outstanding amounts come from AdvanceRecovery rows."""

    __tablename__ = "advances"
    __table_args__ = (
        CheckConstraint("original_amount > 0", name="original_amount_positive"),
        CheckConstraint("recovery_months >= 1", name="recovery_months"),
        CheckConstraint(_one_of("status", ADVANCE_STATUSES), name="status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    original_amount: Mapped[Decimal] = mapped_column(MONEY)
    date_paid: Mapped[date] = mapped_column(Date)
    recovery_months: Mapped[int] = mapped_column(Integer, default=6)
    status: Mapped[str] = mapped_column(String(16), default="active")
    needs_hr_review: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AdvanceInstallment(Base):
    """Scheduled installments. Rescheduling adds a new schedule_version."""

    __tablename__ = "advance_installments"
    __table_args__ = (
        UniqueConstraint("advance_id", "schedule_version", "sequence"),
        UniqueConstraint("advance_id", "schedule_version", "due_year", "due_month"),
        CheckConstraint("due_month BETWEEN 1 AND 12", name="due_month"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("sequence >= 1 AND schedule_version >= 1", name="ordering"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    advance_id: Mapped[int] = mapped_column(ForeignKey("advances.id"), index=True)
    schedule_version: Mapped[int] = mapped_column(Integer, default=1)
    sequence: Mapped[int] = mapped_column(Integer)
    due_year: Mapped[int] = mapped_column(Integer)
    due_month: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AdvanceRecovery(Base):
    """Actual amount recovered from one payroll entry. One row per advance per entry."""

    __tablename__ = "advance_recoveries"
    __table_args__ = (
        UniqueConstraint("advance_id", "payroll_entry_id"),
        CheckConstraint(
            "scheduled_amount >= 0 AND recovered_amount >= 0 AND shortfall_amount >= 0",
            name="amounts_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    advance_id: Mapped[int] = mapped_column(ForeignKey("advances.id"), index=True)
    payroll_entry_id: Mapped[int] = mapped_column(ForeignKey("payroll_entries.id"), index=True)
    scheduled_amount: Mapped[Decimal] = mapped_column(MONEY)
    recovered_amount: Mapped[Decimal] = mapped_column(MONEY)
    shortfall_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    recorded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AuditRecord(Base):
    """Append-only audit trail. Metadata must not contain salary or identifiers."""

    __tablename__ = "audit_records"
    __table_args__ = (Index("ix_audit_records_target", "target_type", "target_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


COMPANY_OWNED = (
    User,
    Department,
    Member,
    Project,
    PayrollEntry,
    PayrollRun,
    Invoice,
    Advance,
    AuditRecord,
    AppSetting,
)


@event.listens_for(Session, "before_flush")
def _stamp_company(session, flush_context, instances):
    """New rows belong to the signed-in company. Tests with one company use company 1."""

    company_id = session.info.get("company_id") or 1
    for obj in session.new:
        if isinstance(obj, COMPANY_OWNED) and getattr(obj, "company_id", None) is None:
            obj.company_id = company_id


@event.listens_for(Session, "do_orm_execute")
def _limit_to_company(execute_state):
    """Hide other companies once a request knows which company is signed in."""

    company_id = execute_state.session.info.get("company_id")
    if company_id is None or not execute_state.is_select:
        return
    execute_state.statement = execute_state.statement.options(
        *[
            with_loader_criteria(
                model,
                lambda cls, cid=company_id: cls.company_id == cid,
                include_aliases=True,
            )
            for model in COMPANY_OWNED
        ]
    )
