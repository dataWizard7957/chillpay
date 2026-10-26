"""Invoice number and approved-snapshot PDF tests."""

from datetime import date
from decimal import Decimal

import pytest

from app.documents.invoice_pdf import InvoiceError, build_invoice_number, build_invoice_view, format_billing_period, format_invoice_date, generate_invoice_pdf


def _approved_snapshot(**overrides):
    snapshot = {
        "status": "approved",
        "member_name": "Raj Kumar",
        "member_type": "contract",
        "invoice_date": date(2026, 10, 3),
        "year": 2026,
        "month": 9,
        "final_leaves": Decimal("1"),
        "final_payment": Decimal("58000.00"),
        "payment_line_label": "Monthly Payment",
        "overtime_pay": Decimal("0.00"),
        "project_allowance": Decimal("5000.00"),
        "project_bonus": Decimal("0.00"),
        "one_off_bonus": Decimal("0.00"),
        "tds": Decimal("6300.00"),
        "amount_paid": Decimal("56700.00"),
        "actual_advance_recovery": Decimal("1000.00"),
    }
    snapshot.update(overrides)
    return snapshot


def test_raj_kumar_september_2026_invoice_number():
    assert build_invoice_number("Raj Kumar", 2026, 9) == "RAKU_INV202609"


def test_one_letter_first_name_uses_available_letter():
    assert build_invoice_number("A Kumar", 2026, 9) == "AKU_INV202609"


def test_regeneration_preserves_the_original_number():
    number = build_invoice_number(
        "Raj Kumar",
        2026,
        9,
        existing_numbers={"RAKU_INV202609", "RAKU_INV202609_2"},
        regenerate_number="RAKU_INV202609",
    )

    assert number == "RAKU_INV202609"


def test_duplicate_for_a_different_invoice_gets_a_suffix():
    first = build_invoice_number("Raj Kumar", 2026, 9, existing_numbers={"RAKU_INV202609"})
    second = build_invoice_number(
        "Raj Kumar",
        2026,
        9,
        existing_numbers={"RAKU_INV202609", "RAKU_INV202609_2"},
    )

    assert first == "RAKU_INV202609_2"
    assert second == "RAKU_INV202609_3"


def test_single_word_name_is_rejected():
    with pytest.raises(InvoiceError, match="single-word"):
        build_invoice_number("Raj", 2026, 9)


def test_hyphenated_name_is_rejected():
    with pytest.raises(InvoiceError, match="not defined"):
        build_invoice_number("Mary-Jane Kumar", 2026, 9)


def test_invoice_date_uses_uppercase_month():
    assert format_invoice_date(date(2026, 10, 3)) == "03-OCT-2026"


def test_september_billing_period_display():
    assert format_billing_period(2026, 9) == "01-Sep-2026 to 30-Sep-2026"


def test_billing_period_uses_the_last_calendar_day():
    assert format_billing_period(2026, 2) == "01-Feb-2026 to 28-Feb-2026"
    assert format_billing_period(2024, 2) == "01-Feb-2024 to 29-Feb-2024"
    assert format_billing_period(2026, 8) == "01-Aug-2026 to 31-Aug-2026"
    assert format_billing_period(2026, 10) == "01-Oct-2026 to 31-Oct-2026"


def test_contract_invoice_lines_omit_zero_optional_lines_and_advances():
    view = build_invoice_view(_approved_snapshot())
    labels = [label for label, _amount in view["lines"]]

    assert view["invoice_number"] == "RAKU_INV202609"
    assert view["invoice_date"] == "03-OCT-2026"
    assert view["billing_period"] == "01-Sep-2026 to 30-Sep-2026"
    assert view["days_absent"] == "1"
    assert ("Project Allowance", "5000.00") in view["lines"]
    assert "Project Bonus" not in labels
    assert "One-Off Bonus" not in labels
    assert "Overtime" not in labels
    assert ("Contract TDS", "6300.00") in view["lines"]
    assert view["amount_paid"] == "56700.00"
    assert all("advance" not in label.casefold() for label in labels)
    assert view["company_name"] == ""


def test_positive_bonus_and_overtime_lines_are_included():
    view = build_invoice_view(
        _approved_snapshot(
            overtime_pay=Decimal("750.00"),
            project_bonus=Decimal("250.00"),
            one_off_bonus=Decimal("1000.00"),
        )
    )
    labels = [label for label, _amount in view["lines"]]

    assert "Overtime" in labels
    assert "Project Bonus" in labels
    assert "One-Off Bonus" in labels


def test_employee_and_trainee_omit_contract_tds():
    employee = build_invoice_view(_approved_snapshot(member_type="employee", tds=Decimal("0.00")))
    trainee = build_invoice_view(
        _approved_snapshot(
            member_type="trainee",
            payment_line_label="Stipend",
            tds=Decimal("0.00"),
        )
    )

    employee_labels = [label for label, _amount in employee["lines"]]
    trainee_labels = [label for label, _amount in trainee["lines"]]
    assert "Contract TDS" not in employee_labels
    assert "Contract TDS" not in trainee_labels
    assert trainee["lines"][0] == ("Stipend", "58000.00")


def test_unapproved_snapshot_is_rejected(tmp_path):
    with pytest.raises(InvoiceError, match="approved"):
        generate_invoice_pdf(_approved_snapshot(status="draft"), tmp_path / "draft.pdf")


def test_approved_snapshot_writes_a_pdf(tmp_path):
    output = tmp_path / "invoice.pdf"
    generate_invoice_pdf(
        _approved_snapshot(company_name="Configured Company", company_address="Mumbai"),
        output,
    )
    contents = output.read_bytes()

    assert contents.startswith(b"%PDF")
    assert b"RAKU_INV202609" in contents
    assert b"Project Allowance" in contents
    assert b"Contract TDS" in contents
    assert b"Advance" not in contents
    assert b"Configured Company" in contents
