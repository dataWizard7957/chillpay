"""Reports read saved snapshots. Temporary databases only."""

from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import Department, DepartmentHeadAssignment, Member, User, utcnow
from app.main import create_app

PASSWORD = "correct-horse"


def _setup(tmp_path):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'reports.sqlite3').as_posix()}"
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


def test_payroll_and_tds_reports_match_the_saved_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("CHILLPAY_INVOICE_DIR", str(tmp_path / "invoices"))
    url, ids = _setup(tmp_path)
    client = _client(url)
    csrf = _login(client)
    member = client.post(
        "/members",
        json={
            "full_name": "Asha Verma",
            "email": "asha@example.test",
            "department_id": ids["department"],
            "member_type": "contract",
            "joining_date": "2024-01-01",
            "pay_term": {
                "effective_from": "2024-01-01",
                "pay_basis": "monthly",
                "monthly_payment": "60000.00",
                "default_project_allowance": "5000.00",
                "default_project_bonus": "0.00",
            },
        },
        headers={"X-CSRF-Token": csrf},
    ).json()["member"]
    entry = client.post(
        "/payroll-entries",
        json={
            "member_id": member["id"],
            "year": 2026,
            "month": 9,
            "total_leaves": "3",
            "extra_work_days": "2",
            "comp_off": "2",
        },
        headers={"X-CSRF-Token": csrf},
    ).json()["payroll_entry"]
    submitted = client.post(
        f"/payroll-entries/{entry['id']}/submit",
        json={"lock_version": entry["lock_version"]},
        headers={"X-CSRF-Token": csrf},
    ).json()["payroll_entry"]
    today = utcnow().date().isoformat()
    assert client.post(
        f"/departments/{ids['department']}/leaves",
        json={"member_id": ids["head"], "start_date": today, "end_date": today},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 200
    approved = client.post(
        f"/payroll-entries/{entry['id']}/approve",
        json={"lock_version": submitted["lock_version"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert approved.status_code == 200
    register = client.get("/reports/payroll-register?year=2026&month=9")
    assert register.status_code == 200
    row = register.json()["report"]["rows"][0]
    assert row["gross_payable"] == "63000.00"
    assert row["tds"] == "6300.00"
    assert row["amount_paid"] == "56700.00"
    tds = client.get("/reports/tds-monthly?year=2026&month=9")
    assert tds.status_code == 200
    assert tds.json()["report"]["rows"][0]["tds"] == "6300.00"
    fy = client.get("/reports/tds-financial-year?financial_year=2026")
    assert fy.status_code == 200
    assert fy.json()["report"]["rows"][0]["amount_paid"] == "56700.00"
    csv_body = client.get("/reports/payroll-register?year=2026&month=9&export=csv")
    assert csv_body.status_code == 200
    assert b"63000.00" in csv_body.content
    xlsx = client.get("/reports/payroll-register?year=2026&month=9&export=xlsx")
    assert xlsx.status_code == 200
    assert xlsx.content[:2] == b"PK"
    dashboard = client.get("/reports/dashboard")
    assert dashboard.json()["dashboard"]["approved_entries"] == 1
    finance = _client(url)
    _login(finance, "finance@example.test")
    finance_tds = finance.get("/reports/tds-monthly?year=2026&month=9")
    assert finance_tds.status_code == 200
    assert finance_tds.json()["report"]["rows"][0]["pan"] in {"", None} or str(
        finance_tds.json()["report"]["rows"][0]["pan"]
    ).startswith("XXXXXX")
    assert finance.get("/audit").status_code == 403
    assert finance.get("/reports/audit").status_code == 403


def test_advance_reports_use_ledger_rows(tmp_path):
    url, ids = _setup(tmp_path)
    client = _client(url)
    csrf = _login(client)
    member = client.post(
        "/members",
        json={
            "full_name": "Asha Verma",
            "email": "asha@example.test",
            "department_id": ids["department"],
            "member_type": "contract",
            "joining_date": "2024-01-01",
            "pay_term": {
                "effective_from": "2024-01-01",
                "pay_basis": "monthly",
                "monthly_payment": "60000.00",
            },
        },
        headers={"X-CSRF-Token": csrf},
    ).json()["member"]
    created = client.post(
        "/advances",
        json={
            "member_id": member["id"],
            "original_amount": "12000.00",
            "date_paid": "2026-08-15",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 200
    ledger = client.get("/reports/advance-ledger")
    assert ledger.json()["report"]["rows"][0]["outstanding"] == "12000.00"
    schedule = client.get("/reports/advance-schedules")
    assert [row["amount"] for row in schedule.json()["report"]["rows"]] == ["2000.00"] * 6
    outstanding = client.get("/reports/outstanding-advances")
    assert outstanding.json()["report"]["rows"][0]["advance_id"] == created.json()["advance"]["id"]
