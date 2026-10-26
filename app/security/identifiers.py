"""Encrypt identifiers at rest and hash them for uniqueness checks.

Fernet uses CHILLPAY_ENCRYPTION_KEY. The lookup hash uses a separate
CHILLPAY_LOOKUP_KEY. PAN is not stored when either key is missing.
"""

import hashlib
import hmac
import os
import re

from cryptography.fernet import Fernet, InvalidToken

PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
AADHAAR_PATTERN = re.compile(r"^[0-9]{12}$")


class IdentifierError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def normalize_pan(value: str) -> str:
    cleaned = value.strip().upper().replace(" ", "")
    if not PAN_PATTERN.fullmatch(cleaned):
        raise IdentifierError("PAN must match AAAAA9999A")
    return cleaned


def normalize_aadhaar(value: str) -> str:
    cleaned = value.strip().replace(" ", "")
    if not AADHAAR_PATTERN.fullmatch(cleaned):
        raise IdentifierError("Aadhaar must be 12 digits")
    return cleaned


def protect(value: str) -> tuple[str, str]:
    """Return ciphertext and a keyed lookup hash. Refuses when keys are missing."""

    ciphertext = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    digest = hmac.new(_lookup_key(), value.encode("utf-8"), hashlib.sha256).hexdigest()
    return ciphertext, digest


def reveal(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise IdentifierError("Stored identifier could not be decrypted", 503) from exc


def mask_pan(pan: str) -> str:
    return "XXXXXX" + pan[-4:]


def mask_aadhaar(aadhaar: str) -> str:
    return f"XXXX XXXX {aadhaar[-4:]}"


def _fernet() -> Fernet:
    raw = os.environ.get("CHILLPAY_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise IdentifierError("Identifier encryption is not configured", 503)
    if raw == os.environ.get("CHILLPAY_LOOKUP_KEY", "").strip():
        raise IdentifierError("Identifier encryption is not configured", 503)
    try:
        return Fernet(raw.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise IdentifierError("Identifier encryption is not configured", 503) from exc


def _lookup_key() -> bytes:
    raw = os.environ.get("CHILLPAY_LOOKUP_KEY", "").strip()
    if not raw:
        raise IdentifierError("Identifier encryption is not configured", 503)
    return raw.encode("utf-8")
