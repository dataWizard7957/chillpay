"""Schema constraints and SQLite triggers. Temporary databases only."""

from datetime import date
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, StatementError

from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_db_engine, create_session_factory, upgrade_database
from app.db.models import (
    Advance,
    AdvanceRecovery,
    AppSetting,
    ApprovalEvent,
    AuditRecord,
    Department,
    DepartmentHeadAssignment,
    Invoice,
    InvoiceVersion,
    Member,
    PayTerm,
    PayrollEntry,
    PayrollRun,
    PayrollSnapshot,
    RunEntry,
    Project,
    ProjectAssignment,
    User,
    Base,
)
from app.services.department import INITIAL_DEPARTMENTS

HEAD_REVISION = "0005_companies"

SEED_SETTINGS = {
    "company_address": "Mumbai",
    "contract_tds_rate": "0.10",
    "overtime_multiplier": "1.5",
    "aadhaar_storage_enabled": "false",
}


@pytest.fixture
def db_url(tmp_path):
    path = tmp_path / "schema.sqlite3"
    url = f"sqlite:///{path.as_posix()}"
    existed = DEFAULT_DATABASE_PATH.exists()
    upgrade_database(url)
    assert DEFAULT_DATABASE_PATH.exists() == existed
    return url


@pytest.fixture
def session(db_url):
    factory = create_session_factory(db_url)
    db_session = factory()
    try:
        yield db_session
    finally:
        db_session.close()


def _department(session, name="Engineering"):
    department = Department(name=name)
    session.add(department)
    session.flush()
    return department


def _member(session, department, *, email="asha.verma@example.test", full_name="Asha Verma"):
    member = Member(
        full_name=full_name,
        email=email,
        department_id=department.id,
        member_type="contract",
        joining_date=date(2024, 1, 1),
    )
    session.add(member)
    session.flush()
    return member


def _user(session, *, email="hr@example.test", role="hr"):
    user = User(email=email, password_hash="not-a-real-hash", role=role)
    session.add(user)
    session.flush()
    return user


def _pay_term(session, member):
    term = PayTerm(
        member_id=member.id,
        effective_from=date(2024, 1, 1),
        pay_basis="monthly",
        monthly_payment=Decimal("60000"),
    )
    session.add(term)
    session.flush()
    return term


def _entry(session, member, *, year=2026, month=9, status="draft"):
    entry = PayrollEntry(member_id=member.id, year=year, month=month, status=status)
    session.add(entry)
    session.flush()
    return entry


def _snapshot(session, entry, term, *, kind="approved", sequence=1):
    snapshot = PayrollSnapshot(
        payroll_entry_id=entry.id,
        sequence=sequence,
        kind=kind,
        pay_term_id=term.id,
        inputs_json="{}",
        results_json="{}",
        gross_payable=Decimal("63000"),
        tds=Decimal("6300"),
        amount_paid=Decimal("56700"),
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def _invoice(session, entry, member, *, invoice_number="ASVE_INV202609"):
    invoice = Invoice(
        invoice_number=invoice_number,
        payroll_entry_id=entry.id,
        member_id=member.id,
        year=entry.year,
        month=entry.month,
    )
    session.add(invoice)
    session.flush()
    return invoice


def test_upgrade_head_records_core_schema_on_a_temporary_database(db_url):
    engine = create_db_engine(db_url)
    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        tables = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
    engine.dispose()

    assert version == HEAD_REVISION
    assert {
        "departments",
        "members",
        "users",
        "auth_sessions",
        "payroll_entries",
        "payroll_snapshots",
        "invoices",
        "invoice_versions",
        "advances",
        "advance_recoveries",
        "audit_records",
        "app_settings",
    }.issubset(tables)


def test_migrated_schema_matches_models(db_url):
    engine = create_db_engine(db_url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diffs = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert diffs == []


def test_upgrade_seeds_initial_departments(session):
    names = [row.name for row in session.scalars(select(Department).order_by(Department.name))]
    assert names == sorted(INITIAL_DEPARTMENTS)
    assert all(row.is_active for row in session.scalars(select(Department)))


def test_upgrade_seeds_known_app_settings_only(session):
    values = {row.key: row.value for row in session.scalars(select(AppSetting))}

    assert values == SEED_SETTINGS
    assert "company_name" not in values
    assert "company_email" not in values
    assert "company_phone" not in values


def test_scaled_decimal_round_trips_through_sqlite(session):
    department = _department(session)
    member = _member(session, department)
    term = _pay_term(session, member)

    session.refresh(term)
    assert term.monthly_payment == Decimal("60000.00")
    assert term.default_project_allowance == Decimal("0.00")


def test_duplicate_monthly_payroll_entry_is_rejected(session):
    department = _department(session)
    member = _member(session, department)
    _entry(session, member, year=2026, month=9)

    session.add(PayrollEntry(member_id=member.id, year=2026, month=9))
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed: payroll_entries"):
        session.flush()


def test_duplicate_invoice_number_is_rejected(session):
    department = _department(session)
    first_member = _member(session, department, email="asha@example.test")
    second_member = _member(
        session, department, email="rakesh@example.test", full_name="Rakesh Kumar"
    )
    first_entry = _entry(session, first_member, month=9, status="approved")
    second_entry = _entry(session, second_member, month=9, status="approved")
    _invoice(session, first_entry, first_member, invoice_number="ASVE_INV202609")

    session.add(
        Invoice(
            invoice_number="ASVE_INV202609",
            payroll_entry_id=second_entry.id,
            member_id=second_member.id,
            year=2026,
            month=9,
        )
    )
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed: invoices.invoice_number"):
        session.flush()


def test_duplicate_advance_recovery_is_rejected(session):
    department = _department(session)
    member = _member(session, department)
    entry = _entry(session, member)
    advance = Advance(
        member_id=member.id,
        original_amount=Decimal("5000"),
        date_paid=date(2026, 9, 1),
    )
    session.add(advance)
    session.flush()
    session.add(
        AdvanceRecovery(
            advance_id=advance.id,
            payroll_entry_id=entry.id,
            scheduled_amount=Decimal("1000"),
            recovered_amount=Decimal("1000"),
        )
    )
    session.flush()

    session.add(
        AdvanceRecovery(
            advance_id=advance.id,
            payroll_entry_id=entry.id,
            scheduled_amount=Decimal("500"),
            recovered_amount=Decimal("500"),
        )
    )
    with pytest.raises(
        IntegrityError, match="UNIQUE constraint failed: advance_recoveries.advance_id"
    ):
        session.flush()


def test_recovery_cannot_exceed_original_advance_amount(session):
    department = _department(session)
    member = _member(session, department)
    first_entry = _entry(session, member, month=9)
    second_entry = _entry(session, member, month=10)
    advance = Advance(
        member_id=member.id,
        original_amount=Decimal("1000"),
        date_paid=date(2026, 9, 1),
    )
    session.add(advance)
    session.flush()
    session.add(
        AdvanceRecovery(
            advance_id=advance.id,
            payroll_entry_id=first_entry.id,
            scheduled_amount=Decimal("600"),
            recovered_amount=Decimal("600"),
        )
    )
    session.flush()

    session.add(
        AdvanceRecovery(
            advance_id=advance.id,
            payroll_entry_id=second_entry.id,
            scheduled_amount=Decimal("500"),
            recovered_amount=Decimal("500"),
        )
    )
    with pytest.raises(IntegrityError, match="exceeds the original advance amount"):
        session.flush()


def test_invoice_requires_approved_or_invoiced_entry(session):
    department = _department(session)
    member = _member(session, department)
    draft = _entry(session, member, status="draft")

    session.add(
        Invoice(
            invoice_number="ASVE_INV202609",
            payroll_entry_id=draft.id,
            member_id=member.id,
            year=2026,
            month=9,
        )
    )
    with pytest.raises(IntegrityError, match="approved payroll entry"):
        session.flush()


def test_invoice_is_allowed_for_an_approved_entry(session):
    department = _department(session)
    member = _member(session, department)
    entry = _entry(session, member, status="approved")
    invoice = _invoice(session, entry, member)
    assert invoice.id is not None


def test_half_day_steps_are_enforced(session):
    department = _department(session)
    member = _member(session, department)
    session.add(
        PayrollEntry(
            member_id=member.id,
            year=2026,
            month=9,
            total_leaves=Decimal("1.2"),
        )
    )
    with pytest.raises(IntegrityError, match="half_day_steps"):
        session.flush()


def test_comp_off_cannot_exceed_leaves_or_extra_days(session):
    department = _department(session)
    member = _member(session, department)
    session.add(
        PayrollEntry(
            member_id=member.id,
            year=2026,
            month=9,
            total_leaves=Decimal("1.0"),
            extra_work_days=Decimal("0.5"),
            comp_off=Decimal("1.0"),
        )
    )
    with pytest.raises(IntegrityError, match="comp_off_bounds"):
        session.flush()


def test_rejection_without_comment_is_rejected(session):
    department = _department(session)
    member = _member(session, department)
    user = _user(session)
    entry = _entry(session, member, status="submitted")

    session.add(
        ApprovalEvent(
            payroll_entry_id=entry.id,
            action="rejected",
            from_status="submitted",
            to_status="rejected",
            actor_user_id=user.id,
            comment=None,
        )
    )
    with pytest.raises(IntegrityError, match="comment_required"):
        session.flush()


def test_rejection_with_blank_comment_is_rejected(session):
    department = _department(session)
    member = _member(session, department)
    user = _user(session)
    entry = _entry(session, member, status="submitted")

    session.add(
        ApprovalEvent(
            payroll_entry_id=entry.id,
            action="rejected",
            from_status="submitted",
            to_status="rejected",
            actor_user_id=user.id,
            comment="   ",
        )
    )
    with pytest.raises(IntegrityError, match="comment_required"):
        session.flush()


def test_orm_rejects_days_with_extra_decimal_places(session):
    department = _department(session)
    member = _member(session, department)
    session.add(
        PayrollEntry(
            member_id=member.id,
            year=2026,
            month=9,
            total_leaves=Decimal("1.25"),
        )
    )
    with pytest.raises(StatementError, match="decimal places"):
        session.flush()


def test_audit_records_are_append_only(session):
    record = AuditRecord(action="login", target_type="session")
    session.add(record)
    session.commit()

    with pytest.raises(IntegrityError, match="append-only"):
        session.execute(
            text("UPDATE audit_records SET action = 'logout' WHERE id = :id"),
            {"id": record.id},
        )
    session.rollback()

    with pytest.raises(IntegrityError, match="append-only"):
        session.execute(text("DELETE FROM audit_records WHERE id = :id"), {"id": record.id})


def test_approval_events_and_snapshots_are_append_only(session):
    department = _department(session)
    member = _member(session, department)
    user = _user(session)
    term = _pay_term(session, member)
    entry = _entry(session, member, status="submitted")
    event = ApprovalEvent(
        payroll_entry_id=entry.id,
        action="submitted",
        from_status="draft",
        to_status="submitted",
        actor_user_id=user.id,
    )
    snapshot = _snapshot(session, entry, term, kind="submitted")
    session.add(event)
    session.commit()

    with pytest.raises(IntegrityError, match="append-only"):
        session.execute(
            text("UPDATE approval_events SET comment = 'changed' WHERE id = :id"),
            {"id": event.id},
        )
    session.rollback()

    with pytest.raises(IntegrityError, match="append-only"):
        session.execute(
            text("UPDATE payroll_snapshots SET amount_paid = 100 WHERE id = :id"),
            {"id": snapshot.id},
        )


def test_department_allows_only_one_open_head_assignment(session):
    department = _department(session)
    first = _member(session, department, email="head1@example.test")
    second = _member(session, department, email="head2@example.test", full_name="Second Head")
    session.add(
        DepartmentHeadAssignment(
            department_id=department.id,
            member_id=first.id,
            start_date=date(2026, 1, 1),
        )
    )
    session.flush()
    session.add(
        DepartmentHeadAssignment(
            department_id=department.id,
            member_id=second.id,
            start_date=date(2026, 2, 1),
        )
    )
    with pytest.raises(
        IntegrityError, match="UNIQUE constraint failed: department_head_assignments.department_id"
    ):
        session.flush()


def test_member_allows_only_one_open_project_assignment(session):
    department = _department(session)
    member = _member(session, department)
    first_project = Project(name="Alpha")
    second_project = Project(name="Beta")
    session.add_all([first_project, second_project])
    session.flush()
    session.add(
        ProjectAssignment(
            member_id=member.id, project_id=first_project.id, start_date=date(2026, 1, 1)
        )
    )
    session.flush()
    session.add(
        ProjectAssignment(
            member_id=member.id, project_id=second_project.id, start_date=date(2026, 2, 1)
        )
    )
    with pytest.raises(
        IntegrityError, match="UNIQUE constraint failed: project_assignments.member_id"
    ):
        session.flush()


def test_invoice_version_can_only_be_superseded(session):
    department = _department(session)
    member = _member(session, department)
    user = _user(session)
    term = _pay_term(session, member)
    entry = _entry(session, member, status="approved")
    snapshot = _snapshot(session, entry, term, kind="approved")
    invoice = _invoice(session, entry, member)
    version = InvoiceVersion(
        invoice_id=invoice.id,
        version=1,
        payroll_snapshot_id=snapshot.id,
        file_path="invoices/ASVE_INV202609.pdf",
        file_sha256="a" * 64,
        generated_by_user_id=user.id,
    )
    session.add(version)
    session.commit()

    version.file_path = "invoices/changed.pdf"
    with pytest.raises(IntegrityError, match="current to superseded"):
        session.flush()
    session.rollback()

    version = session.get(InvoiceVersion, version.id)
    version.status = "superseded"
    session.flush()
    assert version.status == "superseded"


def test_invoice_version_requires_approved_snapshot_of_same_entry(session):
    department = _department(session)
    member = _member(session, department)
    term = _pay_term(session, member)
    entry = _entry(session, member, status="approved")
    submitted = _snapshot(session, entry, term, kind="submitted")
    invoice = _invoice(session, entry, member)

    session.add(
        InvoiceVersion(
            invoice_id=invoice.id,
            version=1,
            payroll_snapshot_id=submitted.id,
            file_path="invoices/ASVE_INV202609.pdf",
            file_sha256="a" * 64,
        )
    )
    with pytest.raises(IntegrityError, match="approved snapshot"):
        session.flush()


def test_one_off_bonus_requires_an_authorizer(session):
    department = _department(session)
    member = _member(session, department)
    session.add(
        PayrollEntry(
            member_id=member.id,
            year=2026,
            month=9,
            one_off_bonus=Decimal("1000"),
        )
    )
    with pytest.raises(IntegrityError, match="one_off_bonus_authorized"):
        session.flush()


def test_mobile_must_be_ten_digits(session):
    department = _department(session)
    session.add(
        Member(
            full_name="Asha Verma",
            email="asha@example.test",
            department_id=department.id,
            member_type="contract",
            joining_date=date(2024, 1, 1),
            mobile="12345",
        )
    )
    with pytest.raises(IntegrityError, match="mobile_ten_digits"):
        session.flush()
