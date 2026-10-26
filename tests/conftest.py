"""Test isolation from developer configuration.

The module-level setup runs before test modules import `app.main`, which
creates the app and loads `.env` at import time. Tests never read the project
`.env` and never inherit CHILLPAY_* values from the developer's shell.
"""

import os
import tempfile
from pathlib import Path

import pytest

import app.config as config

_ISOLATED_ENV_DIR = tempfile.TemporaryDirectory(prefix="chillpay-test-env-")
config.ENV_FILE = Path(_ISOLATED_ENV_DIR.name) / ".env"
for _name in [name for name in os.environ if name.startswith("CHILLPAY_")]:
    del os.environ[_name]

DISABLED_INTEGRATIONS = {
    "CHILLPAY_VOICE": "false",
    "CHILLPAY_TRACING": "false",
    "CHILLPAY_EMAIL": "false",
}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """Give every test the same controlled configuration."""

    for name in [name for name in os.environ if name.startswith("CHILLPAY_")]:
        monkeypatch.delenv(name)
    for name, value in DISABLED_INTEGRATIONS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(config, "ENV_FILE", Path(_ISOLATED_ENV_DIR.name) / ".env")
    monkeypatch.setattr("app.ai.extractor.WARMUP_ENABLED", False)
