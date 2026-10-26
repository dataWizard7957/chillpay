"""Application configuration.

Secrets and company identity are not stored here. Database location can be
overridden with CHILLPAY_DATABASE_URL. Optional integrations default to off.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "chill_pay.sqlite3"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "gemma:2b"
DEFAULT_OLLAMA_KEEP_ALIVE = "30m"
DEFAULT_OLLAMA_READ_TIMEOUT_SECONDS = 90
DEFAULT_OLLAMA_CONNECT_TIMEOUT_SECONDS = 2
DEFAULT_OLLAMA_TAGS_TIMEOUT_SECONDS = 2
DEFAULT_SESSION_COOKIE = "chillpay_session"
DEFAULT_SESSION_IDLE_MINUTES = 30
DEFAULT_SESSION_ABSOLUTE_HOURS = 8
CSRF_HEADER = "X-CSRF-Token"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def load_environment() -> None:
    """Load a local .env file without overriding variables already set."""

    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=False)


def get_database_url() -> str:
    return os.environ.get("CHILLPAY_DATABASE_URL", DEFAULT_DATABASE_URL)


def ollama_model_name() -> str:
    raw = os.environ.get("CHILLPAY_OLLAMA_MODEL", "").strip()
    return raw or DEFAULT_OLLAMA_MODEL


def ollama_base_url() -> str:
    raw = os.environ.get("CHILLPAY_OLLAMA_URL", "").strip()
    return raw or DEFAULT_OLLAMA_URL


def ollama_host_port() -> tuple[str, int]:
    parsed = urlparse(ollama_base_url())
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 11434
    return host, port


def ollama_keep_alive() -> str:
    raw = os.environ.get("CHILLPAY_OLLAMA_KEEP_ALIVE", "").strip()
    return raw or DEFAULT_OLLAMA_KEEP_ALIVE


def ollama_read_timeout_seconds() -> int:
    return _env_int("CHILLPAY_OLLAMA_READ_TIMEOUT_SECONDS", DEFAULT_OLLAMA_READ_TIMEOUT_SECONDS)


def ollama_connect_timeout_seconds() -> int:
    return _env_int("CHILLPAY_OLLAMA_CONNECT_TIMEOUT_SECONDS", DEFAULT_OLLAMA_CONNECT_TIMEOUT_SECONDS)


def ollama_tags_timeout_seconds() -> int:
    return _env_int("CHILLPAY_OLLAMA_TAGS_TIMEOUT_SECONDS", DEFAULT_OLLAMA_TAGS_TIMEOUT_SECONDS)


def integration_flags() -> dict[str, bool]:
    return {
        "voice": _env_flag("CHILLPAY_VOICE"),
        "tracing": _env_flag("CHILLPAY_TRACING"),
        "email": _env_flag("CHILLPAY_EMAIL"),
    }


def session_cookie_name() -> str:
    return os.environ.get("CHILLPAY_SESSION_COOKIE", DEFAULT_SESSION_COOKIE)


def session_idle_minutes() -> int:
    return _env_int("CHILLPAY_SESSION_IDLE_MINUTES", DEFAULT_SESSION_IDLE_MINUTES)


def session_absolute_hours() -> int:
    return _env_int("CHILLPAY_SESSION_ABSOLUTE_HOURS", DEFAULT_SESSION_ABSOLUTE_HOURS)


def session_cookie_secure() -> bool:
    return _env_flag("CHILLPAY_SESSION_SECURE")


def backup_directory() -> Path:
    raw = os.environ.get("CHILLPAY_BACKUP_DIR", "").strip()
    if raw:
        return Path(raw)
    return PROJECT_ROOT / "backups"


def invoice_directory() -> Path:
    raw = os.environ.get("CHILLPAY_INVOICE_DIR", "").strip()
    if raw:
        return Path(raw)
    return PROJECT_ROOT / "data" / "invoices"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "false").strip().casefold() in _TRUE_VALUES
