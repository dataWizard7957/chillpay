# Architecture

Chill Pay is a React + Vite frontend and a FastAPI backend. SQLite is the only database. Payroll amounts are calculated in Python with `Decimal`. The browser never calculates salary, TDS, or recovery.

## Layout

- `app/services/payroll_engine.py` — formulas.
- `app/services/payroll_preview.py` — preview and snapshots.
- `app/services/payroll_workflow.py` — submit, approve, reject, reopen.
- `app/services/reports.py` — reports from saved snapshots and ledger rows.
- `app/ai/extractor.py` — local note extraction. A person must review the result.
- `frontend/src/pages` — screens that call the API with cookie sessions.

## Data

Alembic revisions:

- `0001_baseline`
- `0002_core_schema`
- `0003_seed_departments`

Use `upgrade_database(url)` so the path is not taken from the relative `alembic.ini` URL.

## Optional integrations

`CHILLPAY_VOICE`, `CHILLPAY_TRACING`, and `CHILLPAY_EMAIL` default to false. When they are off, their routes are not registered and their SDKs are not imported.
