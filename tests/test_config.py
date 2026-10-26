"""Environment loading tests. These use a temporary .env file only."""

import os

import app.config as config
from app.main import create_app


def test_load_environment_reads_env_file_without_overriding_existing_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CHILLPAY_EMAIL=true\nCHILLPAY_OLLAMA_MODEL=model-from-env-file\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.setenv("CHILLPAY_EMAIL", "false")
    monkeypatch.setenv("CHILLPAY_OLLAMA_MODEL", "removed-before-load")
    monkeypatch.delenv("CHILLPAY_OLLAMA_MODEL")

    config.load_environment()

    assert os.environ["CHILLPAY_EMAIL"] == "false"
    assert os.environ["CHILLPAY_OLLAMA_MODEL"] == "model-from-env-file"
    assert config.integration_flags()["email"] is False


def test_tests_never_read_the_project_env_file():
    assert config.ENV_FILE != config.PROJECT_ROOT / ".env"
    assert not config.ENV_FILE.exists()


def test_app_creation_in_tests_keeps_integrations_disabled():
    app = create_app()

    assert app.state.integration_flags == {"voice": False, "tracing": False, "email": False}
    assert "CHILLPAY_DATABASE_URL" not in os.environ
