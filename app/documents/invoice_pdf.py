"""Invoice numbers and PDF rendering from an approved payroll snapshot.

Company identity is taken only from the snapshot. This module does not invent
company details and does not recalculate payroll.
"""

import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

MONEY_PLACES = Decimal("0.01")
MONTHS_TITLE = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
class InvoiceError(ValueError):
    """Raised when an invoice number or PDF cannot be produced."""

    def __init__(self, message: str) -> None:
        self.errors = [message]
        super().__init__(message)


def build_invoice_number(
    member_name: str,
    year: int,
    month: int,
    *,
    existing_numbers: set[str] | None = None,
    regenerate_number: str | None = None,
) -> str:
    """Build FIRST_TWO_FIRST_NAME + FIRST_TWO_LAST_NAME + _INV + YYYYMM.

    Regeneration returns the original number unchanged. A single-word name,
    a hyphenated name, or a non-letter name is rejected.
    """

    if regenerate_number:
        return regenerate_number

    _validate_month(year, month)
    first_name, last_name = _split_name(member_name)
    base = f"{_name_letters(first_name)}{_name_letters(last_name)}_INV{year:04d}{month:02d}"
    taken = existing_numbers or set()
    if base not in taken:
        return base

    suffix = 2
    while f"{base}_{suffix}" in taken:
        suffix += 1
    return f"{base}_{suffix}"


def format_invoice_date(value: date) -> str:
    """Format an invoice date as DD-MMM-YYYY with an uppercase month."""

    return f"{value.day:02d}-{MONTHS_TITLE[value.month - 1].upper()}-{value.year}"


def format_billing_period(year: int, month: int) -> str:
    """Format a calendar month as 01-Mmm-YYYY through its last day."""

    _validate_month(year, month)
    last_day = calendar.monthrange(year, month)[1]
    month_label = MONTHS_TITLE[month - 1]
    return f"01-{month_label}-{year} to {last_day:02d}-{month_label}-{year}"


def build_invoice_view(snapshot: dict) -> dict:
    """Return the display data used by the PDF. Payroll values come from the snapshot."""

    _require_approved(snapshot)
    year = int(snapshot["year"])
    month = int(snapshot["month"])
    invoice_number = snapshot.get("invoice_number") or build_invoice_number(
        snapshot["member_name"],
        year,
        month,
        existing_numbers=set(snapshot.get("existing_invoice_numbers") or []),
        regenerate_number=snapshot.get("regenerate_number"),
    )
    lines = _invoice_lines(snapshot)
    return {
        "invoice_number": invoice_number,
        "invoice_date": format_invoice_date(snapshot["invoice_date"]),
        "billing_period": format_billing_period(year, month),
        "member_name": snapshot["member_name"],
        "member_address": snapshot.get("member_address") or "",
        "member_email": snapshot.get("member_email") or "",
        "company_name": snapshot.get("company_name") or "",
        "company_address": snapshot.get("company_address") or "",
        "company_email": snapshot.get("company_email") or "",
        "company_phone": snapshot.get("company_phone") or "",
        "work_description": snapshot.get("work_description") or "",
        "days_absent": _day_text(snapshot["final_leaves"]),
        "lines": lines,
        "amount_paid": _money_text(snapshot["amount_paid"]),
    }


def generate_invoice_pdf(snapshot: dict, output_path: str | Path) -> Path:
    """Write a PDF from an approved snapshot. Returns the output path."""

    view = build_invoice_view(snapshot)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=A4, pageCompression=0)
    y_position = 800

    def draw(text: str) -> None:
        nonlocal y_position
        pdf.drawString(50, y_position, text)
        y_position -= 18

    draw(f"Invoice number: {view['invoice_number']}")
    draw(f"Invoice date: {view['invoice_date']}")
    draw(f"Member: {view['member_name']}")
    if view["member_address"]:
        draw(f"Member address: {view['member_address']}")
    if view["member_email"]:
        draw(f"Member email: {view['member_email']}")
    if view["company_name"]:
        draw(f"Bill to: {view['company_name']}")
    if view["company_address"]:
        draw(f"Bill to address: {view['company_address']}")
    if view["company_email"]:
        draw(f"Company email: {view['company_email']}")
    if view["company_phone"]:
        draw(f"Company phone: {view['company_phone']}")
    if view["work_description"]:
        draw(f"Work description: {view['work_description']}")
    draw(f"Billing period: {view['billing_period']}")
    draw(f"Number of Days Absent: {view['days_absent']}")
    for label, amount in view["lines"]:
        draw(f"{label}: {amount}")
    draw(f"Amount Paid: {view['amount_paid']}")
    pdf.save()
    return path


def _invoice_lines(snapshot: dict) -> list[tuple[str, str]]:
    label = snapshot.get("payment_line_label")
    if not label:
        member_type = str(snapshot.get("member_type", "")).casefold()
        label = "Stipend" if member_type == "trainee" else "Monthly Payment"
    payment_amount = snapshot.get("final_payment")
    if payment_amount is None:
        payment_amount = snapshot.get("regular_pay")
    if payment_amount is None:
        raise InvoiceError("approved snapshot is missing the payment amount")

    lines = [(label, _money_text(payment_amount))]
    _append_positive(lines, "Overtime", snapshot.get("overtime_pay"))
    _append_positive(lines, "Project Allowance", snapshot.get("project_allowance"))
    _append_positive(lines, "Project Bonus", snapshot.get("project_bonus"))
    _append_positive(lines, "One-Off Bonus", snapshot.get("one_off_bonus"))
    if str(snapshot.get("member_type", "")).casefold() == "contract":
        lines.append(("Contract TDS", _money_text(snapshot.get("tds") or 0)))
    return lines


def _append_positive(lines: list[tuple[str, str]], label: str, value) -> None:
    if value is None:
        return
    amount = _money(value)
    if amount > 0:
        lines.append((label, _money_text(amount)))


def _require_approved(snapshot: dict) -> None:
    status = str(snapshot.get("status", "")).casefold()
    if status not in ("approved", "invoiced"):
        raise InvoiceError("invoice generation requires an approved payroll snapshot")


def _split_name(member_name: str) -> tuple[str, str]:
    if not isinstance(member_name, str):
        raise InvoiceError("invoice numbering is not defined for this name")
    parts = member_name.split()
    if len(parts) < 2:
        raise InvoiceError("invoice numbering is not defined for a single-word name")
    return parts[0], parts[-1]


def _name_letters(name_part: str) -> str:
    if not name_part.isalpha():
        raise InvoiceError("invoice numbering is not defined for this name")
    return name_part[:2].upper()


def _validate_month(year: int, month: int) -> None:
    if month < 1 or month > 12:
        raise InvoiceError("month must be from 1 to 12")
    if year < 1:
        raise InvoiceError("year must be positive")


def _money(value) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise InvoiceError("money values must be decimal values")
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return amount.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def _money_text(value) -> str:
    return f"{_money(value):.2f}"


def _day_text(value) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    if amount == amount.to_integral_value():
        return str(amount.to_integral_value())
    return format(amount.normalize(), "f")
