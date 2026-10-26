"""Append-only audit records. Sensitive values are never stored in metadata."""

import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditRecord

DENIED_METADATA_KEYS = frozenset(
    {
        "aadhaar",
        "aadhaar_ciphertext",
        "aadhaar_lookup",
        "amount_paid",
        "comment",
        "comments",
        "csrf_token",
        "emergency_contact",
        "encryption_key",
        "gross_payable",
        "hourly_rate",
        "monthly_payment",
        "net_deposit",
        "note",
        "notes",
        "pan",
        "pan_ciphertext",
        "pan_lookup",
        "password",
        "password_hash",
        "payroll_note",
        "salary",
        "secret",
        "session_token",
        "tds",
        "token",
    }
)


class AuditMetadataError(ValueError):
    """Raised when audit metadata includes a denied field."""


def record_audit(
    db: Session,
    *,
    action: str,
    target_type: str,
    actor_user_id: int | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditRecord:
    payload = None
    if metadata is not None:
        _assert_safe_metadata(metadata)
        payload = json.dumps(metadata, sort_keys=True, default=str)
    record = AuditRecord(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        metadata_json=payload,
    )
    db.add(record)
    db.flush()
    return record


def _assert_safe_metadata(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            name = str(key).strip().casefold()
            location = f"{path}.{name}" if path else name
            if name in DENIED_METADATA_KEYS:
                raise AuditMetadataError(f"audit metadata must not include {location}")
            _assert_safe_metadata(inner, location)
        return
    if isinstance(value, list):
        for index, inner in enumerate(value):
            _assert_safe_metadata(inner, f"{path}[{index}]")
