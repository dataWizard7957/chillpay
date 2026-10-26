"""FastAPI application entry point.

Optional voice, tracing, and email integrations stay disabled unless enabled
in the environment. Their SDKs are not imported here.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.advances import router as advances_router
from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.departments import router as departments_router
from app.api.invoices import router as invoices_router
from app.api.members import router as members_router
from app.api.payroll import router as payroll_router
from app.api.projects import router as projects_router
from app.api.backup import router as backup_router
from app.api.chat import router as chat_router
from app.api.extract import router as extract_router
from app.api.integrations import optional_routers
from app.api.reports import router as reports_router
from app.api.runs import router as runs_router
from app.api.setup import router as setup_router
from app.api.settings import router as settings_router
from app.api.users import router as users_router
from app.ai.extractor import schedule_warmup
from app.config import get_database_url, integration_flags, load_environment
from app.db.database import create_session_factory, upgrade_database

LOCAL_FRONTEND_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5174",
    "http://localhost:5174",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    schedule_warmup()
    yield


def create_app(database_url: str | None = None) -> FastAPI:
    load_environment()
    flags = integration_flags()
    url = database_url or get_database_url()
    upgrade_database(url)
    app = FastAPI(title="Chill Pay", lifespan=lifespan)
    app.state.integration_flags = flags
    app.state.database_url = url
    app.state.session_factory = create_session_factory(url)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(LOCAL_FRONTEND_ORIGINS),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Accept", "Content-Type", "X-CSRF-Token"],
    )
    app.include_router(setup_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(audit_router)
    app.include_router(departments_router)
    app.include_router(projects_router)
    app.include_router(members_router)
    app.include_router(payroll_router)
    app.include_router(runs_router)
    app.include_router(advances_router)
    app.include_router(invoices_router)
    app.include_router(settings_router)
    app.include_router(reports_router)
    app.include_router(extract_router)
    app.include_router(chat_router)
    app.include_router(backup_router)
    for extra in optional_routers():
        app.include_router(extra)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
