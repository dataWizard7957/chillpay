"""Payroll preview, workflow, leave, advances, and invoices. Temporary databases only."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.auth.passwords import hash_password
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import (
    AdvanceInstallment,
    AuditRecord,
    Department,
    DepartmentHeadAssignment,
    InvoiceVersion,
    Member,
    PayrollSnapshot,
    User,
    utcnow,
)
from app.main import create_app
from app.services.advances import split_installments

PASSWORD = "correct-horse"


def test_installment_split_matches_the_required_examples():
    assert split_installments(Decimal("12000.00"), 6) == [Decimal("2000.00")] * 6
    uneven = split_installments(Decimal("10000.00"), 6)
    assert uneven[:5] == [Decimal("1666.67")] * 5
    assert uneven[-1] == Decimal("1666.65")
    assert sum(uneven, Decimal("0.00")) == Decimal("10000.00")


@pytest.fixture
def payroll_db(tmp_path):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'operations.sqlite3').as_posix()}"
    upgrade_database(url)
    session = create_session_factory(url)()
    department = session.scalars(select(Department).where(Department.name == "Development")).one()
    hashed = hash_password(PASSWORD)
    head = Member(
        full_name="Head Person",
        email="head@example.test",
        department_id=department.id,
        member_type="employee",
        joining_date=date(2024, 1, 1),
    )
    session.add(head)
    session.flush()
    session.add(
        DepartmentHeadAssignment(
            department_id=department.id, member_id=head.id, start_date=date(2024, 1, 1)
        )
    )
    session.add_all(
        [
            User(email="hr@example.test", password_hash=hashed, role="hr"),
            User(email="finance@example.test", password_hash=hashed, role="finance"),
            User(
                email="dh@example.test",
                password_hash=hashed,
                role="department_head",
                member_id=head.id,
            ),
        ]
    )
    session.commit()
    ids = {"department": department.id, "head": head.id}
    session.close()
    assert DEFAULT_DATABASE_PATH.exists() == existed
    return url, ids


def _client(url):
    return TestClient(create_app(database_url=url))


def _login(client, email="hr@example.test"):
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _create_member(client, csrf, department_id, **overrides):
    body = {
        "full_name": "Asha Verma",
        "email": "asha@example.test",
        "department_id": department_id,
        "member_type": "contract",
        "joining_date": "2024-01-01",
        "pay_term": {
            "effective_from": "2024-01-01",
            "pay_basis": "monthly",
            "monthly_payment": "60000.00",
            "default_project_allowance": "5000.00",
            "default_project_bonus": "0.00",
        },
    }
    body.update(overrides)
    response = client.post("/members", json=body, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    return response.json()["member"]


def _entry(client, csrf, member_id, month, **overrides):
    body = {
        "member_id": member_id,
        "year": 2026,
        "month": month,
        "total_leaves": "3",
        "extra_work_days": "2",
        "comp_off": "2",
    }
    body.update(overrides)
    response = client.post("/payroll-entries", json=body, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    return response.json()["payroll_entry"]


def _workflow(client, csrf, entry_id, action, lock_version, comment=None):
    body = {"lock_version": lock_version}
    if comment is not None:
        body["comment"] = comment
    return client.post(
        f"/payroll-entries/{entry_id}/{action}",
        json=body,
        headers={"X-CSRF-Token": csrf},
    )


def test_preview_uses_the_engine_and_does_not_prorate_or_persist(payroll_db):
    url, ids = payroll_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    entry = _entry(client, csrf, member["id"], 9)
    preview = client.get(f"/payroll-entries/{entry['id']}/preview")
    assert preview.status_code == 200
    calculation = preview.json()["calculation"]
    assert Decimal(calculation["daily_rate"]) == Decimal("2000")
    assert Decimal(calculation["final_leaves"]) == Decimal("1")
    assert calculation["final_payment"] == "58000.00"
    assert calculation["gross_payable"] == "63000.00"
    assert calculation["tds"] == "6300.00"
    assert calculation["amount_paid"] == "56700.00"
    assert calculation["net_deposit"] == "56700.00"
    assert calculation["recovery_shortfall_flag"] is False

    february = _entry(
        client,
        csrf,
        member["id"],
        2,
        total_leaves="0",
        extra_work_days="0",
        comp_off="0",
    )
    february_preview = client.get(f"/payroll-entries/{february['id']}/preview")
    assert february_preview.status_code == 200
    assert february_preview.json()["calculation"]["final_payment"] == "60000.00"

    session = create_session_factory(url)()
    assert session.scalars(select(PayrollSnapshot)).all() == []
    session.close()
    finance = _client(url)
    _login(finance, "finance@example.test")
    assert finance.get(f"/payroll-entries/{entry['id']}/preview").status_code == 403


def test_approval_follows_head_leave_and_writes_snapshots(payroll_db):
    url, ids = payroll_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    entry = _entry(client, csrf, member["id"], 9)
    submitted = _workflow(client, csrf, entry["id"], "submit", entry["lock_version"])
    assert submitted.status_code == 200
    assert submitted.json()["payroll_entry"]["status"] == "submitted"
    blocked = client.patch(
        f"/payroll-entries/{entry['id']}",
        json={"lock_version": submitted.json()["payroll_entry"]["lock_version"], "total_leaves": "4"},
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked.status_code == 409

    denied = _workflow(
        client, csrf, entry["id"], "approve", submitted.json()["payroll_entry"]["lock_version"]
    )
    assert denied.status_code == 403

    today = utcnow().date().isoformat()
    leave = client.post(
        f"/departments/{ids['department']}/leaves",
        json={
            "member_id": ids["head"],
            "start_date": today,
            "end_date": today,
            "reason": "private reason text",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert leave.status_code == 200

    approved = _workflow(
        client, csrf, entry["id"], "approve", submitted.json()["payroll_entry"]["lock_version"]
    )
    assert approved.status_code == 200
    assert approved.json()["payroll_entry"]["status"] == "approved"

    session = create_session_factory(url)()
    snapshots = session.scalars(select(PayrollSnapshot).order_by(PayrollSnapshot.sequence)).all()
    assert [row.kind for row in snapshots] == ["submitted", "approved"]
    assert snapshots[-1].amount_paid == Decimal("56700.00")
    assert "60000" in snapshots[-1].inputs_json
    audits = session.scalars(select(AuditRecord).where(AuditRecord.action.like("payroll.%"))).all()
    assert audits
    assert all("60000" not in (row.metadata_json or "") for row in audits)
    assert all("private reason text" not in (row.metadata_json or "") for row in session.scalars(select(AuditRecord)))
    with pytest.raises(Exception):
        session.execute(text("UPDATE payroll_snapshots SET kind = 'submitted'"))
        session.commit()
    session.rollback()
    session.close()

    dh = _client(url)
    dh_csrf = _login(dh, "dh@example.test")
    own = client.post(
        f"/members/{ids['head']}/pay-terms",
        json={
            "effective_from": "2024-01-01",
            "pay_basis": "monthly",
            "monthly_payment": "50000.00",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert own.status_code == 200
    head_entry = _entry(client, csrf, ids["head"], 3, total_leaves="0", extra_work_days="0", comp_off="0")
    head_submitted = _workflow(client, csrf, head_entry["id"], "submit", head_entry["lock_version"])
    self_approval = _workflow(
        dh,
        dh_csrf,
        head_entry["id"],
        "approve",
        head_submitted.json()["payroll_entry"]["lock_version"],
    )
    assert self_approval.status_code == 403
    hr_approval = _workflow(
        client,
        csrf,
        head_entry["id"],
        "approve",
        head_submitted.json()["payroll_entry"]["lock_version"],
    )
    assert hr_approval.status_code == 200


def test_rejection_and_reopen_require_a_comment(payroll_db):
    url, ids = payroll_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    entry = _entry(client, csrf, member["id"], 11)
    submitted = _workflow(client, csrf, entry["id"], "submit", entry["lock_version"])
    version = submitted.json()["payroll_entry"]["lock_version"]
    hr_without_leave = _workflow(client, csrf, entry["id"], "reject", version, comment="Not the approver")
    assert hr_without_leave.status_code == 403
    dh = _client(url)
    dh_csrf = _login(dh, "dh@example.test")
    missing = _workflow(dh, dh_csrf, entry["id"], "reject", version, comment="  ")
    assert missing.status_code == 400
    rejected = _workflow(dh, dh_csrf, entry["id"], "reject", version, comment="Hours need review")
    assert rejected.status_code == 200
    assert rejected.json()["payroll_entry"]["status"] == "rejected"

    session = create_session_factory(url)()
    hr = session.scalars(select(User).where(User.email == "hr@example.test")).one()
    session.add(
        User(
            email="asha-user@example.test",
            password_hash=hr.password_hash,
            role="member",
            member_id=member["id"],
        )
    )
    session.commit()
    session.close()

    member_client = _client(url)
    member_csrf = _login(member_client, "asha-user@example.test")
    reopened = _workflow(
        member_client,
        member_csrf,
        entry["id"],
        "reopen",
        rejected.json()["payroll_entry"]["lock_version"],
        comment="Corrected the hours",
    )
    assert reopened.status_code == 200
    assert reopened.json()["payroll_entry"]["status"] == "draft"

    again = _entry(client, csrf, member["id"], 6, total_leaves="0", extra_work_days="0", comp_off="0")
    submitted_again = _workflow(client, csrf, again["id"], "submit", again["lock_version"])
    approved = _workflow(
        dh, dh_csrf, again["id"], "approve", submitted_again.json()["payroll_entry"]["lock_version"]
    )
    assert approved.status_code == 200
    dh_reopen = _workflow(
        dh,
        dh_csrf,
        again["id"],
        "reopen",
        approved.json()["payroll_entry"]["lock_version"],
        comment="Please change this",
    )
    assert dh_reopen.status_code == 403


def test_advances_recover_after_tds_and_keep_history(payroll_db, tmp_path, monkeypatch):
    monkeypatch.setenv("CHILLPAY_INVOICE_DIR", str(tmp_path / "invoices"))
    url, ids = payroll_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    entry = _entry(client, csrf, member["id"], 9)
    created = client.post(
        "/advances",
        json={
            "member_id": member["id"],
            "original_amount": "12000.00",
            "date_paid": "2026-08-15",
            "recovery_months": 6,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 200
    advance = created.json()["advance"]
    assert [row["amount"] for row in advance["installments"]] == ["2000.00"] * 6
    assert advance["installments"][0]["due_year"] == 2026
    assert advance["installments"][0]["due_month"] == 9
    assert advance["outstanding"] == "12000.00"

    uneven = client.post(
        "/advances",
        json={
            "member_id": member["id"],
            "original_amount": "10000.00",
            "date_paid": "2026-01-10",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert uneven.status_code == 200
    amounts = [row["amount"] for row in uneven.json()["advance"]["installments"]]
    assert amounts[:5] == ["1666.67"] * 5
    assert amounts[-1] == "1666.65"

    recovery = client.post(
        f"/payroll-entries/{entry['id']}/recoveries",
        json={"advance_id": advance["id"], "amount": "2000.00"},
        headers={"X-CSRF-Token": csrf},
    )
    assert recovery.status_code == 200
    assert recovery.json()["recovery"]["recovered_amount"] == "2000.00"
    duplicate = client.post(
        f"/payroll-entries/{entry['id']}/recoveries",
        json={"advance_id": advance["id"], "amount": "2000.00"},
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate.status_code == 409
    preview = client.get(f"/payroll-entries/{entry['id']}/preview")
    calculation = preview.json()["calculation"]
    assert calculation["amount_paid"] == "56700.00"
    assert calculation["tds"] == "6300.00"
    assert calculation["actual_advance_recovery"] == "2000.00"
    assert calculation["net_deposit"] == "54700.00"
    assert Decimal(calculation["net_deposit"]) >= 0

    low = _entry(
        client,
        csrf,
        member["id"],
        4,
        total_leaves="30",
        extra_work_days="0",
        comp_off="0",
    )
    short = client.post(
        f"/payroll-entries/{low['id']}/recoveries",
        json={"advance_id": uneven.json()["advance"]["id"], "amount": "10000.00"},
        headers={"X-CSRF-Token": csrf},
    )
    assert short.status_code == 200
    assert Decimal(short.json()["recovery"]["recovered_amount"]) < Decimal("10000.00")
    assert Decimal(short.json()["recovery"]["shortfall_amount"]) > 0
    flagged = client.get(f"/advances/{uneven.json()['advance']['id']}")
    assert flagged.json()["advance"]["needs_hr_review"] is True
    low_preview = client.get(f"/payroll-entries/{low['id']}/preview")
    assert Decimal(low_preview.json()["calculation"]["net_deposit"]) >= 0

    rescheduled = client.post(
        f"/advances/{advance['id']}/reschedule",
        json={"recovery_months": 3, "start_year": 2026, "start_month": 12},
        headers={"X-CSRF-Token": csrf},
    )
    assert rescheduled.status_code == 200
    assert rescheduled.json()["advance"]["outstanding"] == "10000.00"
    assert len(rescheduled.json()["advance"]["installments"]) == 3
    session = create_session_factory(url)()
    versions = {
        row.schedule_version
        for row in session.scalars(
            select(AdvanceInstallment).where(AdvanceInstallment.advance_id == advance["id"])
        )
    }
    assert versions == {1, 2}
    session.close()

    left = client.patch(
        f"/members/{member['id']}",
        json={"last_working_date": "2026-09-30"},
        headers={"X-CSRF-Token": csrf},
    )
    assert left.status_code == 200
    remaining = client.get(f"/advances/{advance['id']}")
    assert remaining.json()["advance"]["needs_hr_review"] is True
    assert remaining.json()["advance"]["outstanding"] == "10000.00"

    today = utcnow().date().isoformat()
    assert client.post(
        f"/departments/{ids['department']}/leaves",
        json={"member_id": ids["head"], "start_date": today, "end_date": today},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 200
    submitted = _workflow(client, csrf, entry["id"], "submit", entry["lock_version"])
    approved = _workflow(
        client, csrf, entry["id"], "approve", submitted.json()["payroll_entry"]["lock_version"]
    )
    assert approved.status_code == 200
    invoice = client.post(
        f"/payroll-entries/{entry['id']}/invoice", headers={"X-CSRF-Token": csrf}
    )
    assert invoice.status_code == 200, invoice.text
    file_response = client.get(f"/invoices/{invoice.json()['invoice']['id']}/file")
    assert file_response.status_code == 200
    assert b"Advance" not in file_response.content
    assert b"Amount Paid: 56700.00" in file_response.content


def test_invoice_keeps_its_number_for_every_month_length(payroll_db, tmp_path, monkeypatch):
    monkeypatch.setenv("CHILLPAY_INVOICE_DIR", str(tmp_path / "invoices"))
    url, ids = payroll_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    entry = _entry(client, csrf, member["id"], 9)
    submitted = _workflow(client, csrf, entry["id"], "submit", entry["lock_version"])
    today = utcnow().date().isoformat()
    assert client.post(
        f"/departments/{ids['department']}/leaves",
        json={"member_id": ids["head"], "start_date": today, "end_date": today},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 200
    approved = _workflow(
        client, csrf, entry["id"], "approve", submitted.json()["payroll_entry"]["lock_version"]
    )
    assert approved.status_code == 200
    first = client.post(f"/payroll-entries/{entry['id']}/invoice", headers={"X-CSRF-Token": csrf})
    assert first.status_code == 200, first.text
    assert first.json()["invoice"]["invoice_number"] == "ASVE_INV202609"
    assert first.json()["invoice"]["version"] == 1
    second = client.post(f"/payroll-entries/{entry['id']}/invoice", headers={"X-CSRF-Token": csrf})
    assert second.status_code == 200, second.text
    assert second.json()["invoice"]["invoice_number"] == "ASVE_INV202609"
    assert second.json()["invoice"]["version"] == 2

    session = create_session_factory(url)()
    versions = session.scalars(select(InvoiceVersion).order_by(InvoiceVersion.version)).all()
    assert [row.status for row in versions] == ["superseded", "current"]
    assert versions[0].file_sha256 != versions[1].file_sha256 or versions[0].file_path != versions[1].file_path
    contents = open(versions[1].file_path, "rb").read()
    session.close()
    assert b"Number of Days Absent: 1" in contents
    assert b"Contract TDS: 6300.00" in contents
    assert b"Project Allowance: 5000.00" in contents
    assert b"Bill to address: Mumbai" in contents
    assert b"Company email:" not in contents
    assert b"Company phone:" not in contents

    october = _entry(client, csrf, member["id"], 10, total_leaves="0", extra_work_days="0", comp_off="0")
    october_submitted = _workflow(client, csrf, october["id"], "submit", october["lock_version"])
    october_approved = _workflow(
        client,
        csrf,
        october["id"],
        "approve",
        october_submitted.json()["payroll_entry"]["lock_version"],
    )
    assert october_approved.status_code == 200
    october_invoice = client.post(
        f"/payroll-entries/{october['id']}/invoice", headers={"X-CSRF-Token": csrf}
    )
    assert october_invoice.status_code == 200
    assert october_invoice.json()["invoice"]["invoice_number"].endswith("202610")

    finance = _client(url)
    _login(finance, "finance@example.test")
    assert finance.get("/invoices").status_code == 200
    assert finance.get("/members").status_code == 403
    assert finance.post(
        f"/payroll-entries/{entry['id']}/invoice", headers={"X-CSRF-Token": "missing"}
    ).status_code == 403
    current = client.get(f"/payroll-entries/{entry['id']}").json()["payroll_entry"]
    reopen = _workflow(
        client,
        csrf,
        entry["id"],
        "reopen",
        current["lock_version"],
        comment="Invoice was issued",
    )
    assert reopen.status_code == 409
    assert reopen.json()["detail"] == "Invoiced entries cannot be reopened"


def test_settings_leave_company_identity_blank_and_aadhaar_off(payroll_db):
    url, _ids = payroll_db
    client = _client(url)
    csrf = _login(client)
    current = client.get("/settings")
    assert current.status_code == 200
    assert current.json()["settings"]["company_name"] == ""
    assert current.json()["settings"]["company_email"] == ""
    assert current.json()["settings"]["company_phone"] == ""
    assert current.json()["settings"]["company_address"] == "Mumbai"
    assert current.json()["settings"]["aadhaar_storage_enabled"] == "false"
    refused = client.patch(
        "/settings",
        json={"aadhaar_storage_enabled": "true"},
        headers={"X-CSRF-Token": csrf},
    )
    assert refused.status_code == 400
    assert client.get("/settings").json()["settings"]["aadhaar_storage_enabled"] == "false"
    finance = _client(url)
    _login(finance, "finance@example.test")
    assert finance.get("/settings").status_code == 403
