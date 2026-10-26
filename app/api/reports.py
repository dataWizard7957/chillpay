"""Saved-record reports and exports."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db, require_user
from app.db.models import User
from app.services.errors import ServiceError
from app.services.exports import csv_bytes, xlsx_bytes
from app.services.reports import REPORTS, build_report, dashboard

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/dashboard")
def get_dashboard(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return {"dashboard": dashboard(db, user)}


@router.get("")
def list_reports(user: User = Depends(require_user)) -> dict:
    names = list(REPORTS)
    if user.role != "hr":
        names = [name for name in names if name != "audit"]
    if user.role == "member":
        names = [name for name in names if name not in {"tds-monthly", "tds-financial-year"}]
    return {"reports": [{"name": name, "title": REPORTS[name]} for name in names]}


@router.get("/{name}")
def get_report(
    name: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    year: int | None = None,
    month: int | None = None,
    financial_year: int | None = None,
    export: str | None = Query(default=None),
):
    try:
        report = build_report(
            db, user, name, year=year, month=month, financial_year=financial_year
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if export == "csv":
        return Response(
            csv_bytes(report["headers"], report["rows"]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
        )
    if export == "xlsx":
        return Response(
            xlsx_bytes(report["headers"], report["rows"], report["title"]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
        )
    return {"report": report}
