# Chill Pay

Chill Pay is a local payroll and invoice app. Each account is its own company. Notes for a month are reviewed by a person, then Python calculates the pay and creates one invoice per member.

The chat model only reads a note. It does not calculate money. Pay is calculated in `app/services/payroll_engine.py`.

## What you can do

The side panel has four pages.

**This month**

1. Notes. Type a payroll note in chat, or paste it on the monthly run. A saved member can be matched by full name, or by a first name when only one member has that first name.
2. Review. Check leaves, extra days, and comp-off. The amount on the row is calculated by code.
3. Decisions. A Department Head approves or rejects a submitted entry with a comment. HR can approve a monthly run when that department has no head yet. After a head is assigned, HR can approve only while that head is on leave.

**People.** Members, departments, and projects. A new member needs a name, email, department, type (employee, contract, or trainee), joining date, and pay. Address and work description are needed before an invoice.

**Records.** Past months, invoices, reports, and advances. Reports include the payroll register, monthly TDS, financial-year TDS, and CSV export.

**Setup.** Company name, address, email, and phone (printed on invoices), login accounts, and Department Head leave.

Finance sees Records only. A member or a Department Head sees This month and Records. HR sees all four pages.

## Pay

Python does maths

## Setup

Use Python 3.12 and Node.js. On this machine Python is `py -3.12`.

From the `ChillPay` folder:

```
py -3.12 -m pip install -e ".[dev]"
cd frontend
npm install
```

Copy `.env.example` to `.env` if you want to change defaults. The app runs without that file. The database is created at `data/chill_pay.sqlite3`. Starting the API upgrades the database.

Optional, before storing PAN:

```
py -3.12 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put one value in `CHILLPAY_ENCRYPTION_KEY` and a different value in `CHILLPAY_LOOKUP_KEY`. Do not commit those keys. Aadhaar storage stays off until it is explicitly enabled.

Chat notes use a local Ollama server at `http://127.0.0.1:11434`, model `gemma:2b`. If Ollama is not running, typed notes can still be saved and edited by hand. Nothing is sent to a cloud model.

## Run

Use two terminals.

API:

```
py -3.12 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

UI, from `frontend`:

```
npm run dev
```

Open `http://127.0.0.1:5173/`. The API health check is `http://127.0.0.1:8000/health`.

On the sign-in screen, choose **Create a company account**. Enter an email, a password of at least 8 characters, and a company name. That creates an empty company and signs you in as its HR user. A later sign-in must use that same password. Another email creates a different company and cannot see this company's members, pay, or invoices.

If this database already had members before company accounts, those rows belong to the company named Existing company. Sign in with the password from the first time that email was created.

`py -3.12 -m app.cli create-admin` creates one HR user only when the database has no users yet.

## Tests

```
py -3.12 -m pytest -q
cd frontend
npm test
```

## Layout

- `app/services/payroll_engine.py` calculates pay.
- `app/ai/extractor.py` reads names and numbers from a note.
- `app/services/payroll_runs.py` is the monthly run: collect, review, approve, invoice.
- `app/documents/invoice_pdf.py` builds the invoice number and PDF.
- `frontend/src/pages/Workspace.jsx` is the four-page screen.
- `docs/` has longer notes on architecture, security, and backup. Where a note there disagrees with this file, this file describes the app as it runs now.
