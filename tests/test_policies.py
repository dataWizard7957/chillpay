"""Authorization policy tests. Temporary databases only."""

from datetime import date

import pytest
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.auth.policies import (
    can_access_member_record,
    can_approve_payroll_entry,
    can_write,
    current_head_assignment,
    is_explicitly_on_leave,
)
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import (
    Department,
    DepartmentHeadAssignment,
    DepartmentHeadLeave,
    Member,
    PayrollEntry,
    User,
)

ON_DATE = date(2026, 10, 16)
PASSWORD = "correct-horse"


@pytest.fixture
def db(tmp_path):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'policies.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    try:
        yield session
    finally:
        session.close()
        assert DEFAULT_DATABASE_PATH.exists() == existed


def _seed(session):
    engineering = Department(name="Engineering")
    design = Department(name="Design")
    session.add_all([engineering, design])
    session.flush()

    head = Member(
        full_name="Head Engineer",
        email="head.eng@example.test",
        department_id=engineering.id,
        member_type="employee",
        joining_date=date(2024, 1, 1),
    )
    worker = Member(
        full_name="Asha Verma",
        email="asha@example.test",
        department_id=engineering.id,
        member_type="contract",
        joining_date=date(2024, 1, 1),
    )
    other_head = Member(
        full_name="Head Design",
        email="head.design@example.test",
        department_id=design.id,
        member_type="employee",
        joining_date=date(2024, 1, 1),
    )
    other = Member(
        full_name="Rakesh Kumar",
        email="rakesh@example.test",
        department_id=design.id,
        member_type="employee",
        joining_date=date(2024, 1, 1),
    )
    session.add_all([head, worker, other_head, other])
    session.flush()

    hashed = hash_password(PASSWORD)
    hr = User(email="hr@example.test", password_hash=hashed, role="hr")
    finance = User(email="finance@example.test", password_hash=hashed, role="finance")
    dh = User(
        email="dh@example.test", password_hash=hashed, role="department_head", member_id=head.id
    )
    other_dh = User(
        email="dh2@example.test",
        password_hash=hashed,
        role="department_head",
        member_id=other_head.id,
    )
    member_user = User(
        email="member@example.test", password_hash=hashed, role="member", member_id=worker.id
    )
    session.add_all([hr, finance, dh, other_dh, member_user])
    session.flush()

    session.add_all(
        [
            DepartmentHeadAssignment(
                department_id=engineering.id,
                member_id=head.id,
                start_date=date(2026, 1, 1),
            ),
            DepartmentHeadAssignment(
                department_id=design.id,
                member_id=other_head.id,
                start_date=date(2026, 1, 1),
            ),
        ]
    )
    session.flush()

    worker_entry = PayrollEntry(member_id=worker.id, year=2026, month=10, status="submitted")
    head_entry = PayrollEntry(member_id=head.id, year=2026, month=10, status="submitted")
    other_entry = PayrollEntry(member_id=other.id, year=2026, month=10, status="submitted")
    session.add_all([worker_entry, head_entry, other_entry])
    session.flush()

    return {
        "engineering": engineering,
        "design": design,
        "head": head,
        "worker": worker,
        "other": other,
        "hr": hr,
        "finance": finance,
        "dh": dh,
        "other_dh": other_dh,
        "member_user": member_user,
        "worker_entry": worker_entry,
        "head_entry": head_entry,
        "other_entry": other_entry,
    }


def test_department_head_approves_only_their_department(db):
    data = _seed(db)

    assert can_approve_payroll_entry(db, data["dh"], data["worker_entry"], ON_DATE) is True
    assert can_approve_payroll_entry(db, data["dh"], data["other_entry"], ON_DATE) is False
    assert can_approve_payroll_entry(db, data["other_dh"], data["worker_entry"], ON_DATE) is False


def test_department_head_cannot_approve_their_own_entry(db):
    data = _seed(db)

    assert can_approve_payroll_entry(db, data["dh"], data["head_entry"], ON_DATE) is False


def test_hr_approves_department_head_entries(db):
    data = _seed(db)

    assert can_approve_payroll_entry(db, data["hr"], data["head_entry"], ON_DATE) is True


def test_hr_can_approve_when_the_department_has_no_head(db):
    data = _seed(db)
    for row in list(db.scalars(select(DepartmentHeadAssignment))):
        if row.department_id == data["engineering"].id:
            db.delete(row)
    db.flush()

    assert current_head_assignment(db, data["engineering"].id, ON_DATE) is None
    assert can_approve_payroll_entry(db, data["hr"], data["worker_entry"], ON_DATE) is True


def test_hr_cannot_approve_when_the_head_is_not_on_leave(db):
    data = _seed(db)

    assert can_approve_payroll_entry(db, data["hr"], data["worker_entry"], ON_DATE) is False
    assert is_explicitly_on_leave(db, data["engineering"].id, data["head"].id, ON_DATE) is False


def test_hr_can_approve_only_when_the_head_is_explicitly_on_leave(db):
    data = _seed(db)
    db.add(
        DepartmentHeadLeave(
            department_id=data["engineering"].id,
            member_id=data["head"].id,
            start_date=date(2026, 10, 10),
            end_date=date(2026, 10, 20),
            created_by_user_id=data["hr"].id,
        )
    )
    db.flush()

    assert can_approve_payroll_entry(db, data["hr"], data["worker_entry"], ON_DATE) is True
    assert can_approve_payroll_entry(db, data["hr"], data["worker_entry"], date(2026, 10, 9)) is False
    assert can_approve_payroll_entry(db, data["hr"], data["other_entry"], ON_DATE) is False


def test_cancelled_leave_is_not_treated_as_on_leave(db):
    data = _seed(db)
    from app.db.models import utcnow

    db.add(
        DepartmentHeadLeave(
            department_id=data["engineering"].id,
            member_id=data["head"].id,
            start_date=date(2026, 10, 10),
            end_date=date(2026, 10, 20),
            created_by_user_id=data["hr"].id,
            cancelled_at=utcnow(),
            cancelled_by_user_id=data["hr"].id,
        )
    )
    db.flush()

    assert is_explicitly_on_leave(db, data["engineering"].id, data["head"].id, ON_DATE) is False
    assert can_approve_payroll_entry(db, data["hr"], data["worker_entry"], ON_DATE) is False


def test_members_access_only_their_own_records(db):
    data = _seed(db)

    assert can_access_member_record(db, data["member_user"], data["worker"], ON_DATE) is True
    assert can_access_member_record(db, data["member_user"], data["head"], ON_DATE) is False
    assert can_access_member_record(db, data["member_user"], data["other"], ON_DATE) is False
    assert can_approve_payroll_entry(db, data["member_user"], data["worker_entry"], ON_DATE) is False


def test_department_head_can_access_own_department_members_only(db):
    data = _seed(db)

    assert can_access_member_record(db, data["dh"], data["worker"], ON_DATE) is True
    assert can_access_member_record(db, data["dh"], data["head"], ON_DATE) is True
    assert can_access_member_record(db, data["dh"], data["other"], ON_DATE) is False


def test_hr_can_access_members_and_finance_cannot(db):
    data = _seed(db)

    assert can_access_member_record(db, data["hr"], data["other"], ON_DATE) is True
    assert can_access_member_record(db, data["finance"], data["worker"], ON_DATE) is False


def test_finance_is_read_only_and_cannot_approve(db):
    data = _seed(db)

    assert can_write(data["finance"]) is False
    assert can_write(data["hr"]) is True
    assert can_approve_payroll_entry(db, data["finance"], data["worker_entry"], ON_DATE) is False
    assert can_approve_payroll_entry(db, data["finance"], data["head_entry"], ON_DATE) is False
