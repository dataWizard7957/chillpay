"""Optional integrations stay disabled and do not load external SDKs."""

import sys

from app.config import integration_flags
from app.main import create_app


def test_integration_flags_default_to_disabled(monkeypatch):
    monkeypatch.delenv("CHILLPAY_VOICE", raising=False)
    monkeypatch.delenv("CHILLPAY_TRACING", raising=False)
    monkeypatch.delenv("CHILLPAY_EMAIL", raising=False)

    assert integration_flags() == {"voice": False, "tracing": False, "email": False}


def test_disabled_integrations_do_not_import_optional_sdks():
    sys.modules.pop("elevenlabs", None)
    sys.modules.pop("sentry_sdk", None)
    app = create_app()

    assert app.state.integration_flags == {"voice": False, "tracing": False, "email": False}
    assert "elevenlabs" not in sys.modules
    assert "sentry_sdk" not in sys.modules
    paths = {route.path for route in app.routes}
    assert "/integrations/voice/speak" not in paths
    assert "/integrations/email/invoices/{invoice_id}" not in paths


def test_enabled_integrations_do_not_load_sdks_or_send(monkeypatch):
    monkeypatch.setenv("CHILLPAY_VOICE", "true")
    monkeypatch.setenv("CHILLPAY_TRACING", "true")
    monkeypatch.setenv("CHILLPAY_EMAIL", "true")
    sys.modules.pop("elevenlabs", None)
    sys.modules.pop("sentry_sdk", None)
    app = create_app()
    assert "elevenlabs" not in sys.modules
    assert "sentry_sdk" not in sys.modules
    from fastapi.testclient import TestClient

    client = TestClient(app)
    assert client.post("/integrations/voice/speak").status_code == 501
    assert client.post("/integrations/email/invoices/1").status_code == 501
    status = client.get("/integrations/tracing/status")
    assert status.status_code == 200
    assert status.json()["sending"] is False
