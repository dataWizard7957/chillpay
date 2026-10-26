"""Optional integrations. Routes exist only when the matching flag is on."""

from fastapi import APIRouter, HTTPException

from app.config import integration_flags

NOT_CONFIGURED = "This optional integration is enabled but not configured."


def optional_routers() -> list:
    flags = integration_flags()
    routers = []
    if flags["voice"]:
        routers.append(_voice_router())
    if flags["tracing"]:
        routers.append(_tracing_router())
    if flags["email"]:
        routers.append(_email_router())
    return routers


def _voice_router() -> APIRouter:
    router = APIRouter(prefix="/integrations/voice", tags=["integrations"])

    @router.post("/speak")
    def speak() -> None:
        raise HTTPException(status_code=501, detail=NOT_CONFIGURED)

    return router


def _tracing_router() -> APIRouter:
    router = APIRouter(prefix="/integrations/tracing", tags=["integrations"])

    @router.get("/status")
    def status() -> dict:
        return {"enabled": True, "sending": False, "detail": NOT_CONFIGURED}

    return router


def _email_router() -> APIRouter:
    router = APIRouter(prefix="/integrations/email", tags=["integrations"])

    @router.post("/invoices/{invoice_id}")
    def send_invoice(invoice_id: int) -> None:
        raise HTTPException(status_code=501, detail=NOT_CONFIGURED)

    return router
