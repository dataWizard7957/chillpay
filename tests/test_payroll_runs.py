"""Monthly payroll run workflow. Fake extractor only. Temporary databases."""

import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.config import DEFAULT_DATABASE_PATH
from app.db.database import create_session_factory, upgrade_database
from app.db.models import (
    AppSetting,
    AuditRecord,
    Department,
    DepartmentHeadAssignment,
    Member,
    PayTerm,
    User,
)
from app.main import create_app

PASSWORD = "correct-horse"


@pytest.fixture
def run_db(tmp_path):
    existed = DEFAULT_DATABASE_PATH.exists()
    url = f"sqlite:///{(tmp_path / 'runs.sqlite3').as_posix()}"
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
        address="12 Lane",
        work_description="Monthly work",
    )
    session.add(head)
    session.flush()
    session.add(
        DepartmentHeadAssignment(
            department_id=department.id, member_id=head.id, start_date=date(2024, 1, 1)
        )
    )
    session.add(
        PayTerm(
            member_id=head.id,
            effective_from=date(2024, 1, 1),
            pay_basis="monthly",
            monthly_payment=Decimal("60000"),
            default_project_allowance=Decimal("0"),
            default_project_bonus=Decimal("0"),
        )
    )
    for key, value in (
        ("company_name", "Chill"),
        ("company_address", "12 Lane"),
        ("company_email", "billing@example.test"),
        ("company_phone", "9999999999"),
    ):
        row = session.get(AppSetting, (1, key))
        if row is None:
            session.add(AppSetting(company_id=1, key=key, value=value))
        else:
            row.value = value
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
        "full_name": "Raj Kumar",
        "email": "raj@example.test",
        "department_id": department_id,
        "member_type": "contract",
        "joining_date": "2024-01-01",
        "address": "12 Lane",
        "work_description": "Monthly work",
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
    assert response.status_code == 200
    return response.json()["member"]


def _member_payload(name, **values):
    body = {
        "member_name": name,
        "evidence": values.pop("evidence", "from the note"),
        "total_leaves": 0,
        "extra_work_days": 0,
        "comp_off": 0,
        "hours_worked": 0,
        "overtime_hours": 0,
        "one_off_bonus": 0,
        "advance_amount": 0,
    }
    body.update(values)
    return body


def _fake_extractor(monkeypatch, members):
    def client(_request):
        return json.dumps({"members": members})

    monkeypatch.setattr("app.api.runs.ollama_client", client)


def _create_run(client, csrf, year=2026, month=9, confirm=False):
    response = client.post(
        "/runs",
        json={"year": year, "month": month, "confirm": confirm},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    return response.json()["run"]


def test_cannot_skip_steps_or_invoice_before_approval(run_db, monkeypatch):
    url, ids = run_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    run = _create_run(client, csrf)
    assert client.post(f"/runs/{run['id']}/approve", headers={"X-CSRF-Token": csrf}).status_code == 409
    assert client.post(f"/runs/{run['id']}/invoices", headers={"X-CSRF-Token": csrf}).status_code == 409
    _fake_extractor(monkeypatch, [_member_payload(member["full_name"], total_leaves=1)])
    extracted = client.post(
        f"/runs/{run['id']}/extract",
        json={"notes_text": "Raj Kumar: 1 leave"},
        headers={"X-CSRF-Token": csrf},
    )
    assert extracted.status_code == 200
    assert extracted.json()["run"]["status"] == "review"
    assert client.post(f"/runs/{run['id']}/invoices", headers={"X-CSRF-Token": csrf}).status_code == 409


def test_cannot_approve_with_validation_error(run_db, monkeypatch):
    url, ids = run_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    run = _create_run(client, csrf)
    _fake_extractor(
        monkeypatch,
        [_member_payload(member["full_name"], total_leaves=3, extra_work_days=1, comp_off=2)],
    )
    extracted = client.post(
        f"/runs/{run['id']}/extract",
        json={"notes_text": "Raj Kumar: 3 leaves"},
        headers={"X-CSRF-Token": csrf},
    )
    assert extracted.status_code == 200
    errors = extracted.json()["run"]["entries"][0]["validation_errors"]
    assert any("comp_off cannot exceed extra_work_days" in item for item in errors)
    dh = _client(url)
    dh_csrf = _login(dh, "dh@example.test")
    denied = dh.post(f"/runs/{run['id']}/approve", headers={"X-CSRF-Token": dh_csrf})
    assert denied.status_code == 409


def test_unknown_names_are_dropped(run_db, monkeypatch):
    url, ids = run_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    run = _create_run(client, csrf)
    _fake_extractor(
        monkeypatch,
        [
            _member_payload("Stranger", total_leaves=9),
            _member_payload(member["full_name"], total_leaves=2, evidence="2 leaves"),
        ],
    )
    extracted = client.post(
        f"/runs/{run['id']}/extract",
        json={"notes_text": "Raj Kumar 2 leaves"},
        headers={"X-CSRF-Token": csrf},
    )
    assert extracted.status_code == 200
    names = [row["member_name"] for row in extracted.json()["run"]["entries"]]
    assert names == ["Raj Kumar"]


def test_first_name_matches_one_member_and_unknown_only_stays_on_collect(run_db, monkeypatch):
    url, ids = run_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    run = _create_run(client, csrf)
    _fake_extractor(monkeypatch, [_member_payload("Raj", total_leaves=2, evidence="2 leaves")])
    matched = client.post(
        f"/runs/{run['id']}/extract",
        json={"notes_text": "Raj 2 leaves"},
        headers={"X-CSRF-Token": csrf},
    )
    assert matched.status_code == 200
    assert matched.json()["run"]["status"] == "review"
    assert [row["member_name"] for row in matched.json()["run"]["entries"]] == ["Raj Kumar"]

    other = _create_member(
        client,
        csrf,
        ids["department"],
        full_name="Raj Singh",
        email="raj.singh@example.test",
    )
    empty = _create_run(client, csrf, confirm=True)
    _fake_extractor(monkeypatch, [_member_payload("Raj", total_leaves=1)])
    missed = client.post(
        f"/runs/{empty['id']}/extract",
        json={"notes_text": "Raj 1 leave"},
        headers={"X-CSRF-Token": csrf},
    )
    assert missed.status_code == 200
    assert missed.json()["run"]["status"] == "collect"
    assert missed.json()["run"]["entries"] == []
    assert missed.json()["run"]["unmatched_names"] == ["Raj"]
    assert other["full_name"] == "Raj Singh"

    stranger = _create_run(client, csrf, confirm=True)
    _fake_extractor(monkeypatch, [_member_payload("Stranger", total_leaves=4)])
    unknown = client.post(
        f"/runs/{stranger['id']}/extract",
        json={"notes_text": "Stranger 4 leaves"},
        headers={"X-CSRF-Token": csrf},
    )
    assert unknown.status_code == 200
    assert unknown.json()["run"]["status"] == "collect"
    assert unknown.json()["run"]["entries"] == []
    assert unknown.json()["run"]["unmatched_names"] == ["Stranger"]


def test_invoice_succeeds_after_a_one_word_name_gets_a_last_name(run_db, monkeypatch):
    url, ids = run_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(
        client,
        csrf,
        ids["department"],
        full_name="Raj",
        email="raj.one@example.test",
    )
    run = _create_run(client, csrf)
    _fake_extractor(monkeypatch, [_member_payload("Raj", total_leaves=1)])
    extracted = client.post(
        f"/runs/{run['id']}/extract",
        json={"notes_text": "Raj 1 leave"},
        headers={"X-CSRF-Token": csrf},
    )
    assert extracted.json()["run"]["entries"][0]["member_name"] == "Raj"
    dh = _client(url)
    dh_csrf = _login(dh, "dh@example.test")
    approved = dh.post(f"/runs/{run['id']}/approve", headers={"X-CSRF-Token": dh_csrf})
    assert approved.status_code == 200
    blocked = client.post(f"/runs/{run['id']}/invoices", headers={"X-CSRF-Token": csrf})
    assert blocked.status_code == 400
    assert "single-word" in blocked.json()["detail"]
    renamed = client.patch(
        f"/members/{member['id']}",
        json={"full_name": "Raj Kumar"},
        headers={"X-CSRF-Token": csrf},
    )
    assert renamed.status_code == 200
    invoices = client.post(f"/runs/{run['id']}/invoices", headers={"X-CSRF-Token": csrf})
    assert invoices.status_code == 200
    assert invoices.json()["run"]["status"] == "invoiced"
    assert invoices.json()["run"]["invoices"][0]["invoice_number"].startswith("RAKU_INV202609")


def test_negatives_and_non_numeric_are_clamped_to_zero(run_db, monkeypatch):
    url, ids = run_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    run = _create_run(client, csrf)
    _fake_extractor(
        monkeypatch,
        [
            _member_payload(
                member["full_name"],
                total_leaves=-3,
                extra_work_days="nope",
                overtime_hours=-4,
                one_off_bonus="abc",
                advance_amount=-9,
            )
        ],
    )
    extracted = client.post(
        f"/runs/{run['id']}/extract",
        json={"notes_text": "Raj Kumar"},
        headers={"X-CSRF-Token": csrf},
    )
    row = extracted.json()["run"]["entries"][0]
    assert row["total_leaves"] == "0.0"
    assert row["extra_work_days"] == "0.0"
    assert row["overtime_hours"] == "0.00"
    assert row["one_off_bonus"] == "0.00"
    assert row["advance_amount"] == "0.00"


def test_comp_off_and_half_day_rules(run_db, monkeypatch):
    url, ids = run_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    run = _create_run(client, csrf)
    _fake_extractor(monkeypatch, [_member_payload(member["full_name"], total_leaves=1)])
    client.post(
        f"/runs/{run['id']}/extract",
        json={"notes_text": "Raj Kumar: 1 leave"},
        headers={"X-CSRF-Token": csrf},
    )
    bad_comp = client.patch(
        f"/runs/{run['id']}/entries/{member['id']}",
        json={"total_leaves": "3", "extra_work_days": "1", "comp_off": "2"},
        headers={"X-CSRF-Token": csrf},
    )
    assert any("extra_work_days" in item for item in bad_comp.json()["run"]["entries"][0]["validation_errors"])
    bad_step = client.patch(
        f"/runs/{run['id']}/entries/{member['id']}",
        json={"total_leaves": "1.3", "extra_work_days": "0", "comp_off": "0"},
        headers={"X-CSRF-Token": csrf},
    )
    assert any("0.5" in item for item in bad_step.json()["run"]["entries"][0]["validation_errors"])
    ok = client.patch(
        f"/runs/{run['id']}/entries/{member['id']}",
        json={"total_leaves": "1.5", "extra_work_days": "0", "comp_off": "0"},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.json()["run"]["entries"][0]["validation_errors"] == []
    assert ok.json()["run"]["entries"][0]["total_leaves"] == "1.5"


def test_worked_example_contract_run(run_db, monkeypatch):
    url, ids = run_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    run = _create_run(client, csrf)
    _fake_extractor(
        monkeypatch,
        [
            _member_payload(
                member["full_name"],
                evidence="3 leaves, 2 extra days, convert 2 to comp-off",
                total_leaves=3,
                extra_work_days=2,
                comp_off=2,
            )
        ],
    )
    extracted = client.post(
        f"/runs/{run['id']}/extract",
        json={"notes_text": "Raj Kumar: 3 leaves, 2 extra days, convert 2 to comp-off."},
        headers={"X-CSRF-Token": csrf},
    )
    row = extracted.json()["run"]["entries"][0]
    assert row["payment"] == "58000.00"
    assert row["allowance"] == "5000.00"
    assert row["gross"] == "63000.00"
    assert row["tds"] == "6300.00"
    assert row["amount_paid"] == "56700.00"
    dh = _client(url)
    dh_csrf = _login(dh, "dh@example.test")
    approved = dh.post(f"/runs/{run['id']}/approve", headers={"X-CSRF-Token": dh_csrf})
    assert approved.status_code == 200
    assert approved.json()["run"]["status"] == "approve"
    assert approved.json()["message"] == "Approved."
    invoices = client.post(f"/runs/{run['id']}/invoices", headers={"X-CSRF-Token": csrf})
    assert invoices.status_code == 200
    assert invoices.json()["run"]["status"] == "invoiced"
    number = invoices.json()["run"]["invoices"][0]["invoice_number"]
    assert number.startswith("RAKU_INV202609")
    pdf = client.get(f"/invoices/{invoices.json()['run']['invoices'][0]['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    locked = client.patch(
        f"/runs/{run['id']}/entries/{member['id']}",
        json={"total_leaves": "1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert locked.status_code == 409
    session = create_session_factory(url)()
    audit = session.scalars(select(AuditRecord).where(AuditRecord.action == "payroll_run.approved")).one()
    assert "58000" not in (audit.metadata_json or "")
    assert "Raj" not in (audit.metadata_json or "")
    session.close()


def test_department_head_cannot_approve_own_entry_and_hr_needs_leave_for_others(run_db, monkeypatch):
    url, ids = run_db
    client = _client(url)
    csrf = _login(client)
    member = _create_member(client, csrf, ids["department"])
    run = _create_run(client, csrf)
    _fake_extractor(monkeypatch, [_member_payload(member["full_name"], total_leaves=1)])
    client.post(
        f"/runs/{run['id']}/extract",
        json={"notes_text": "Raj Kumar: 1 leave"},
        headers={"X-CSRF-Token": csrf},
    )
    hr_blocked = client.post(f"/runs/{run['id']}/approve", headers={"X-CSRF-Token": csrf})
    assert hr_blocked.status_code == 403
    today = date.today().isoformat()
    leave = client.post(
        f"/departments/{ids['department']}/leaves",
        json={"member_id": ids["head"], "start_date": today, "end_date": today, "reason": "cover"},
        headers={"X-CSRF-Token": csrf},
    )
    assert leave.status_code == 200
    hr_ok = client.post(f"/runs/{run['id']}/approve", headers={"X-CSRF-Token": csrf})
    assert hr_ok.status_code == 200

    own = _create_run(client, csrf, month=6, confirm=True)
    _fake_extractor(monkeypatch, [_member_payload("Head Person", total_leaves=1)])
    extracted = client.post(
        f"/runs/{own['id']}/extract",
        json={"notes_text": "Head Person: 1 leave"},
        headers={"X-CSRF-Token": csrf},
    )
    assert extracted.status_code == 200
    assert extracted.json()["run"]["entries"][0]["member_name"] == "Head Person"
    dh = _client(url)
    dh_csrf = _login(dh, "dh@example.test")
    denied = dh.post(f"/runs/{own['id']}/approve", headers={"X-CSRF-Token": dh_csrf})
    assert denied.status_code == 403


def test_member_cannot_read_another_members_run_or_invoice(run_db, monkeypatch):
    url, ids = run_db
    client = _client(url)
    csrf = _login(client)
    raj = _create_member(client, csrf, ids["department"])
    asha = _create_member(
        client,
        csrf,
        ids["department"],
        full_name="Asha Verma",
        email="asha@example.test",
    )
    client.post(
        "/users",
        json={"email": "raj.user@example.test", "password": PASSWORD, "role": "member", "member_id": raj["id"]},
        headers={"X-CSRF-Token": csrf},
    )
    client.post(
        "/users",
        json={"email": "asha.user@example.test", "password": PASSWORD, "role": "member", "member_id": asha["id"]},
        headers={"X-CSRF-Token": csrf},
    )
    run = _create_run(client, csrf)
    _fake_extractor(
        monkeypatch,
        [
            _member_payload(raj["full_name"], total_leaves=1),
            _member_payload(asha["full_name"], total_leaves=2),
        ],
    )
    client.post(
        f"/runs/{run['id']}/extract",
        json={"notes_text": "both"},
        headers={"X-CSRF-Token": csrf},
    )
    today = date.today().isoformat()
    client.post(
        f"/departments/{ids['department']}/leaves",
        json={"member_id": ids["head"], "start_date": today, "end_date": today, "reason": "cover"},
        headers={"X-CSRF-Token": csrf},
    )
    client.post(f"/runs/{run['id']}/approve", headers={"X-CSRF-Token": csrf})
    invoiced = client.post(f"/runs/{run['id']}/invoices", headers={"X-CSRF-Token": csrf})
    invoices = invoiced.json()["run"]["invoices"]
    asha_invoice = next(row for row in invoices if row["member_id"] == asha["id"])
    raj_client = _client(url)
    _login(raj_client, "raj.user@example.test")
    seen = raj_client.get(f"/runs/{run['id']}")
    assert seen.status_code == 200
    names = [row["member_name"] for row in seen.json()["run"]["entries"]]
    assert names == ["Raj Kumar"]
    hidden = raj_client.get(f"/invoices/{asha_invoice['id']}/pdf")
    assert hidden.status_code == 404


def test_extractor_failure_does_not_save_entries(run_db, monkeypatch):
    url, ids = run_db
    client = _client(url)
    csrf = _login(client)
    run = _create_run(client, csrf)

    def fail(_request):
        from app.ai.extractor import ModelUnavailableError

        raise ModelUnavailableError("Ollama is not running on 127.0.0.1:11434")

    monkeypatch.setattr("app.api.runs.ollama_client", fail)
    response = client.post(
        f"/runs/{run['id']}/extract",
        json={"notes_text": "Raj Kumar: 1 leave"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 503
    assert "not running" in response.json()["detail"].casefold()
    current = client.get(f"/runs/{run['id']}")
    assert current.json()["run"]["status"] == "collect"
    assert current.json()["run"]["entries"] == []


def test_extractor_loading_and_cutoff_messages(run_db, monkeypatch):
    url, ids = run_db
    client = _client(url)
    csrf = _login(client)
    run = _create_run(client, csrf)

    def loading(_request):
        from app.ai.extractor import ModelUnavailableError

        raise ModelUnavailableError(
            "The local model is still loading. Try again, or enter payroll values manually."
        )

    monkeypatch.setattr("app.api.runs.ollama_client", loading)
    still_loading = client.post(
        f"/runs/{run['id']}/extract",
        json={"notes_text": "Raj Kumar: 1 leave"},
        headers={"X-CSRF-Token": csrf},
    )
    assert still_loading.status_code == 503
    assert "still loading" in still_loading.json()["detail"].casefold()

    def cutoff(_request):
        from app.ai.extractor import ExtractionError

        raise ExtractionError("The reply was cut off")

    monkeypatch.setattr("app.api.runs.ollama_client", cutoff)
    cut = client.post(
        f"/runs/{run['id']}/extract",
        json={"notes_text": "Raj Kumar: 1 leave"},
        headers={"X-CSRF-Token": csrf},
    )
    assert cut.status_code == 400
    assert "cut off" in cut.json()["detail"].casefold()
    current = client.get(f"/runs/{run['id']}")
    assert current.json()["run"]["status"] == "collect"
    assert current.json()["run"]["entries"] == []


def test_delete_removes_an_open_run(run_db):
    url, _ids = run_db
    client = _client(url)
    csrf = _login(client)
    run = _create_run(client, csrf)
    deleted = client.delete(f"/runs/{run['id']}", headers={"X-CSRF-Token": csrf})
    assert deleted.status_code == 200
    assert client.get(f"/runs/{run['id']}").status_code == 404
