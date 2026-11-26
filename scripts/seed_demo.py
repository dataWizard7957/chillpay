"""Fill an empty database with made-up demo data. Safe to run on every start.

Adds a fictional company profile, a few members, and last month's payroll run
waiting for review. No invoices are created: visitors approve the run and make
them from the app. Does nothing if the company already has members, or when
sign-in is required (the data would land in an account nobody can sign in to).

    py -3.12 -m scripts.seed_demo
"""

from datetime import date

from fastapi.testclient import TestClient

from app.config import auth_disabled, load_environment
from app.main import create_app

COMPANY = {
    "company_name": "Demo Studio Pvt Ltd",
    "company_address": "12 Example Lane, Pune 411001",
    "company_email": "accounts@demo-studio.example",
    "company_phone": "+91 00000 00000",
}

# (name, email, department, type, monthly pay, work)
MEMBERS = [
    ("Asha Verma", "asha@demo-studio.example", "Development", "employee", "85000", "Backend development"),
    ("Rohan Mehta", "rohan@demo-studio.example", "Digitisation", "employee", "60000", "Document digitisation"),
    ("Kiran Rao", "kiran@demo-studio.example", "Development", "contract", "70000", "Mobile app contract"),
    ("Neha Joshi", "neha@demo-studio.example", "Admin", "trainee", "20000", "Office operations trainee"),
]

# Last month's notes, entered as reviewed values (no AI needed).
ENTRIES = {
    "Asha Verma": {"evidence": "Took 2 days leave", "total_leaves": "2"},
    "Rohan Mehta": {"evidence": "Worked 1 extra day for the launch", "extra_work_days": "1"},
    "Kiran Rao": {"evidence": "Full month, Rs 5000 bonus", "one_off_bonus": "5000"},
    "Neha Joshi": {"evidence": "Full month"},
}


def _check(response):
    if response.status_code >= 300:
        raise SystemExit(f"demo seed failed: {response.request.url.path} {response.status_code} {response.text[:300]}")
    return response.json()


def main() -> None:
    load_environment()
    if not auth_disabled():
        print("demo seed skipped: sign-in is required")
        return
    client = TestClient(create_app())
    if _check(client.get("/members"))["members"]:
        print("demo seed skipped: data already exists")
        return

    _check(client.patch("/settings", json=COMPANY))
    departments = {d["name"]: d["id"] for d in _check(client.get("/departments"))["departments"]}
    today = date.today()
    joined = date(today.year - 1, 4, 1).isoformat()
    member_ids = {}
    for name, email, dept, member_type, pay, work in MEMBERS:
        body = {
            "full_name": name,
            "email": email,
            "department_id": departments[dept],
            "member_type": member_type,
            "joining_date": joined,
            "address": "Example address, Pune",
            "work_description": work,
            "pay_term": {"effective_from": joined, "pay_basis": "monthly", "monthly_payment": pay},
        }
        member_ids[name] = _check(client.post("/members", json=body))["member"]["id"]

    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    run = _check(client.post("/runs", json={"year": year, "month": month, "confirm": True}))
    run_id = run.get("run", run)["id"]
    for name, fields in ENTRIES.items():
        _check(client.patch(f"/runs/{run_id}/entries/{member_ids[name]}", json=fields))
    print(f"demo seed added {len(MEMBERS)} members and a {year}-{month:02d} run for review")


if __name__ == "__main__":
    main()
