"""Local payroll-note extraction.

The language model only proposes field values. Python validation decides
whether those values are usable, and a person must review them before they
are saved or submitted.
"""

import http.client
import json
import logging
import threading
import time
from decimal import Decimal

from app.ai.prompts import build_extraction_prompt
from app.config import (
    ollama_base_url,
    ollama_connect_timeout_seconds,
    ollama_host_port,
    ollama_keep_alive,
    ollama_model_name,
    ollama_read_timeout_seconds,
    ollama_tags_timeout_seconds,
)
from app.services.payroll_validation import PayrollValidationError, validate_payroll_inputs

NUM_PREDICT_PER_MEMBER = 100
NUM_PREDICT_MIN = 256
NUM_PREDICT_MAX = 1500
OLLAMA_WARMUP_NUM_PREDICT = 1
WARMUP_ENABLED = True
_CUTOFF_MESSAGE = "The reply was cut off"
_log = logging.getLogger(__name__)
_warmup_lock = threading.Lock()
_warmup_thread: threading.Thread | None = None
_warmed_up = False
_last_generate = {"done_reason": None, "response": None}
NUMBER_FIELDS = (
    "total_leaves",
    "extra_work_days",
    "comp_off",
    "hours_worked",
    "overtime_hours",
    "one_off_bonus",
    "advance_amount",
)
OLLAMA_RESPONSE_FORMAT = {
    "type": "object",
    "properties": {
        "members": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "member_name": {"type": "string"},
                    "evidence": {"type": "string"},
                    "total_leaves": {"type": "number"},
                    "extra_work_days": {"type": "number"},
                    "comp_off": {"type": "number"},
                    "hours_worked": {"type": "number"},
                    "overtime_hours": {"type": "number"},
                    "one_off_bonus": {"type": "number"},
                    "advance_amount": {"type": "number"},
                },
                "required": [
                    "member_name",
                    "evidence",
                    "total_leaves",
                    "extra_work_days",
                    "comp_off",
                    "hours_worked",
                    "overtime_hours",
                    "one_off_bonus",
                    "advance_amount",
                ],
            },
        }
    },
    "required": ["members"],
}
ALLOWED_FIELDS = (
    "member_name",
    "member_id",
    "evidence",
    "total_leaves",
    "extra_work_days",
    "comp_off",
    "overtime_hours",
    "hours_worked",
    "project_allowance",
    "project_bonus",
    "one_off_bonus",
    "advance_amount",
)
DAY_FIELDS = ("total_leaves", "extra_work_days", "comp_off")
MONEY_FIELDS = ("project_allowance", "project_bonus", "one_off_bonus", "advance_amount")
FORBIDDEN_FIELDS = (
    "gross_payable",
    "final_payment",
    "regular_pay",
    "tds",
    "amount_paid",
    "net_deposit",
    "salary",
    "approved",
    "invoice_number",
    "actual_advance_recovery",
)


class ExtractionError(ValueError):
    """Raised when model output cannot be turned into a reviewable extraction."""

    def __init__(self, message: str) -> None:
        self.errors = [message]
        super().__init__(message)


class ModelUnavailableError(ExtractionError):
    """Raised when the local model cannot be reached. Manual entry still works."""


class ConnectTimeoutError(TimeoutError):
    """TCP connect to Ollama exceeded the connect timeout."""


class ReadTimeoutError(TimeoutError):
    """Ollama accepted the connection but did not finish the body in time."""


def extract_payroll_note(note: str, *, model_client, known_members=None, model_name: str | None = None) -> dict:
    """Return candidate fields from a payroll note.

    `model_client` receives a request dict and returns the model text.
    The result always requires human review and is never marked saved.
    """

    if not isinstance(note, str) or not note.strip():
        raise ExtractionError("payroll note is empty")

    names = _known_names(known_members)
    prompt = build_extraction_prompt(note, known_names=names)
    member_count = len(names)
    model_request = {
        "model": model_name or ollama_model_name(),
        "prompt": prompt,
        "temperature": 0,
        "member_count": member_count,
    }
    try:
        raw_response = model_client(model_request)
    except ModelUnavailableError:
        raise
    except ExtractionError:
        raise
    except (OSError, TimeoutError) as exc:
        _log.warning("Local model request failed: %s", exc.__class__.__name__)
        raise ModelUnavailableError(_unavailable_message(exc)) from exc
    except Exception as exc:
        _log.warning("Local model failed: %s", exc.__class__.__name__)
        raise ExtractionError("local model failed") from exc

    parsed = _parse_json_object(raw_response)
    members, unmatched_names = _clean_members(parsed, known_members)
    validation_errors = []
    for member in members:
        validation_errors.extend(_validate_extracted_fields(member))
    first = members[0] if members else None
    fields = _fields_from_member(first)
    member_match = first["member_match"] if first else "unassigned"
    member_id = first.get("member_id") if first else None
    return {
        "fields": fields,
        "members": members,
        "evidence": first.get("evidence", "") if first else "",
        "validation_errors": validation_errors,
        "member_match": member_match,
        "member_id": member_id,
        "requires_human_review": True,
        "saved": False,
        "submitted": False,
        "original_note": note,
        "model": model_request["model"],
        "temperature": model_request["temperature"],
        "unmatched_names": unmatched_names,
    }


def ollama_client(model_request: dict) -> str:
    """Call a local Ollama server. Non-local URLs are rejected."""

    base_url = ollama_base_url().rstrip("/")
    if not _is_local_url(base_url):
        raise ModelUnavailableError("Ollama URL must stay on the local machine")
    schedule_warmup()
    _precheck_tags()
    body = _generate(
        model_request["model"],
        model_request["prompt"],
        temperature=model_request["temperature"],
        num_predict=num_predict_for_members(model_request.get("member_count")),
        response_format=OLLAMA_RESPONSE_FORMAT,
    )
    _last_generate["done_reason"] = body.get("done_reason")
    _last_generate["response"] = body.get("response")
    if str(body.get("done_reason") or "").casefold() == "length":
        raise ExtractionError(_CUTOFF_MESSAGE)
    text = body.get("response")
    if not isinstance(text, str):
        raise ExtractionError("local model returned an unexpected response")
    return text


def last_generate_info() -> dict:
    return {"done_reason": _last_generate["done_reason"], "response": _last_generate["response"]}


def _known_names(known_members) -> list[str]:
    if not known_members:
        return []
    names = []
    for member in known_members:
        if isinstance(member, str):
            name = member.strip()
        else:
            name = str(member.get("name", "")).strip()
        if name:
            names.append(name)
    return names


def _clean_members(parsed: dict, known_members) -> tuple[list[dict], list[str]]:
    raw_members = parsed.get("members")
    if not isinstance(raw_members, list):
        if any(key in parsed for key in ("member_name", *NUMBER_FIELDS, *MONEY_FIELDS)):
            raw_members = [parsed]
        else:
            raw_members = []
    cleaned = []
    unmatched = []
    for item in raw_members:
        if not isinstance(item, dict):
            continue
        name = item.get("member_name")
        name = name.strip() if isinstance(name, str) else ""
        match = _match_member(name or None, known_members)
        if known_members is not None and match["status"] in {"unknown", "ambiguous"} and name:
            unmatched.append(name)
        if known_members is not None:
            if match["status"] not in {"matched", "ambiguous"}:
                continue
        evidence = item.get("evidence")
        record = {
            "member_name": name,
            "evidence": evidence.strip() if isinstance(evidence, str) else "",
            "member_id": match["member_id"],
            "member_match": match["status"] if known_members is not None else "unassigned",
        }
        if match["status"] == "matched" and known_members is not None:
            for member in known_members:
                if isinstance(member, dict) and member.get("id") == match["member_id"]:
                    record["member_name"] = member.get("name") or name
                    break
        for field_name in NUMBER_FIELDS:
            record[field_name] = _numeric_value(item.get(field_name))
        for extra in ("project_allowance", "project_bonus"):
            if extra in item:
                record[extra] = _numeric_value(item.get(extra))
        cleaned.append(record)
    return cleaned, unmatched


def _fields_from_member(member: dict | None) -> dict:
    if member is None:
        return {}
    fields = {}
    if member.get("member_name"):
        fields["member_name"] = member["member_name"]
    if member.get("evidence"):
        fields["evidence"] = member["evidence"]
    for key in (*NUMBER_FIELDS, "project_allowance", "project_bonus"):
        if key in member and member[key] != Decimal("0"):
            fields[key] = member[key]
    return fields


def _numeric_value(value) -> Decimal:
    if value is None or value == "" or isinstance(value, bool):
        return Decimal("0")
    try:
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            number = Decimal(str(value))
        elif isinstance(value, str):
            number = Decimal(value.strip())
        else:
            return Decimal("0")
    except Exception:
        return Decimal("0")
    if number < 0:
        return Decimal("0")
    return number


def num_predict_for_members(member_count) -> int:
    try:
        count = int(member_count or 0)
    except (TypeError, ValueError):
        count = 0
    return min(NUM_PREDICT_MAX, max(NUM_PREDICT_MIN, NUM_PREDICT_PER_MEMBER * max(count, 0)))


def schedule_warmup() -> None:
    """Load the local model in a background thread. Returns immediately."""

    if not WARMUP_ENABLED:
        return
    global _warmup_thread
    with _warmup_lock:
        if _warmed_up:
            return
        if _warmup_thread is not None and _warmup_thread.is_alive():
            return
        thread = threading.Thread(target=_run_warmup, name="ollama-warmup", daemon=True)
        _warmup_thread = thread
        thread.start()


def warmup_for_monthly_run() -> None:
    """Kick off warm-up when the monthly payroll page opens."""

    schedule_warmup()


def _run_warmup() -> None:
    global _warmed_up
    try:
        if not _is_local_url(ollama_base_url().rstrip("/")):
            return
        _precheck_tags()
        _generate(ollama_model_name(), "ok", temperature=0, num_predict=OLLAMA_WARMUP_NUM_PREDICT)
    except Exception as exc:
        _log.warning("Ollama warmup failed: %s", exc.__class__.__name__)
        return
    with _warmup_lock:
        _warmed_up = True


def _precheck_tags() -> None:
    try:
        _http_json(
            "GET",
            "/api/tags",
            connect_timeout=ollama_connect_timeout_seconds(),
            read_timeout=ollama_tags_timeout_seconds(),
        )
    except ModelUnavailableError as exc:
        raise ModelUnavailableError(_not_running_message()) from exc
    except (ConnectTimeoutError, OSError, ReadTimeoutError, TimeoutError) as exc:
        raise ModelUnavailableError(_not_running_message()) from exc


def _generate(
    model: str,
    prompt: str,
    *,
    temperature: float,
    num_predict: int,
    response_format: dict | None = None,
) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": ollama_keep_alive(),
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if response_format is not None:
        payload["format"] = response_format
    try:
        return _http_json(
            "POST",
            "/api/generate",
            payload,
            connect_timeout=ollama_connect_timeout_seconds(),
            read_timeout=ollama_read_timeout_seconds(),
        )
    except ConnectTimeoutError as exc:
        raise ModelUnavailableError(_not_running_message()) from exc
    except ReadTimeoutError as exc:
        raise ModelUnavailableError(_model_loading_message()) from exc
    except OSError as exc:
        raise ModelUnavailableError(_classify_generate_failure(exc)) from exc


def _http_json(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    connect_timeout: float,
    read_timeout: float,
) -> dict:
    """HTTP JSON to local Ollama. Does not use urllib, so proxy env vars are ignored."""

    host, port = ollama_host_port()
    started = time.perf_counter()
    connect_s = None
    first_byte_s = None
    conn = http.client.HTTPConnection(host, port, timeout=connect_timeout)
    try:
        try:
            conn.connect()
        except TimeoutError as exc:
            raise ConnectTimeoutError("connect timed out") from exc
        connect_s = time.perf_counter() - started
        if conn.sock is not None:
            conn.sock.settimeout(read_timeout)
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        try:
            response = conn.getresponse()
            first_byte_s = time.perf_counter() - started
            raw = response.read()
        except TimeoutError as exc:
            raise ReadTimeoutError("read timed out") from exc
        total_s = time.perf_counter() - started
        _log.warning(
            "Ollama %s %s %s:%s connect=%.3fs first_byte=%.3fs total=%.3fs status=%s",
            method,
            path,
            host,
            port,
            connect_s,
            first_byte_s,
            total_s,
            response.status,
        )
        text = raw.decode("utf-8", errors="replace")
        if response.status >= 400:
            _log.warning("Ollama HTTP %s on %s", response.status, path)
            message = _http_error_message(response.status, text[:300])
            raise ModelUnavailableError(message)
        if not text.strip():
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExtractionError("local model returned an unexpected response") from exc
        if not isinstance(parsed, dict):
            raise ExtractionError("local model returned an unexpected response")
        return parsed
    except (ConnectTimeoutError, ReadTimeoutError, ModelUnavailableError, ExtractionError) as exc:
        elapsed = time.perf_counter() - started
        _log.warning(
            "Ollama %s %s %s:%s failed after %.3fs connect=%s first_byte=%s: %s",
            method,
            path,
            host,
            port,
            elapsed,
            _fmt_timing(connect_s),
            _fmt_timing(first_byte_s),
            exc.__class__.__name__,
        )
        raise
    except OSError as exc:
        elapsed = time.perf_counter() - started
        _log.warning(
            "Ollama %s %s %s:%s failed after %.3fs connect=%s first_byte=%s: %s",
            method,
            path,
            host,
            port,
            elapsed,
            _fmt_timing(connect_s),
            _fmt_timing(first_byte_s),
            exc.__class__.__name__,
        )
        raise
    finally:
        conn.close()


def _fmt_timing(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}s"


def _is_connect_failure(exc: BaseException) -> bool:
    if isinstance(exc, ConnectTimeoutError):
        return True
    if isinstance(exc, ConnectionRefusedError):
        return True
    text = str(exc).casefold()
    return "refused" in text or "10061" in text or "cannot connect" in text


def _classify_generate_failure(exc: BaseException) -> str:
    if _is_connect_failure(exc) or isinstance(exc, ConnectTimeoutError):
        return _not_running_message()
    return _model_loading_message()


def _http_error_message(status: int, detail: str) -> str:
    lowered = detail.casefold()
    if status in {404, 502, 503} or "loading" in lowered or "not found" in lowered:
        if "loading" in lowered:
            return _model_loading_message()
        if status == 404:
            return _model_loading_message()
    if _is_connect_failure(Exception(detail)):
        return _not_running_message()
    return _model_loading_message() if status >= 500 else _not_running_message()


def _not_running_message() -> str:
    host, port = ollama_host_port()
    return f"Ollama is not running on {host}:{port}"


def _model_loading_message() -> str:
    return "The local model is still loading. Try again, or enter payroll values manually."


def _parse_json_object(raw_response) -> dict:
    if not isinstance(raw_response, str):
        raise ExtractionError("local model returned malformed JSON")
    text = raw_response.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractionError("local model returned malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise ExtractionError("local model returned malformed JSON")
    return parsed


def _validate_extracted_fields(fields: dict) -> list[str]:
    errors = []
    day_values = {}
    for field_name in DAY_FIELDS:
        if field_name not in fields:
            continue
        try:
            day_values[field_name] = _decimal_value(fields[field_name], field_name)
            fields[field_name] = day_values[field_name]
        except ExtractionError as exc:
            errors.extend(exc.errors)

    if day_values:
        try:
            validate_payroll_inputs(
                pay_basis="monthly",
                monthly_payment=Decimal("0"),
                total_leaves=day_values.get("total_leaves"),
                extra_work_days=day_values.get("extra_work_days"),
                comp_off=day_values.get("comp_off"),
            )
        except PayrollValidationError as exc:
            errors.extend(exc.errors)

    for field_name in ("overtime_hours", "hours_worked", *MONEY_FIELDS):
        if field_name not in fields:
            continue
        try:
            amount = _decimal_value(fields[field_name], field_name)
            if amount < 0:
                errors.append(f"{field_name} cannot be negative")
            else:
                fields[field_name] = amount
        except ExtractionError as exc:
            errors.extend(exc.errors)
    return errors


def _decimal_value(value, field_name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ExtractionError(f"{field_name} must be a decimal value")
    try:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, int):
            return Decimal(value)
        if isinstance(value, str):
            return Decimal(value.strip())
    except Exception as exc:
        raise ExtractionError(f"{field_name} must be a decimal value") from exc
    raise ExtractionError(f"{field_name} must be a decimal value")


def _match_member(member_name, known_members) -> dict:
    if known_members is None or member_name is None:
        return {"status": "unassigned", "member_id": None}
    if not isinstance(member_name, str) or not member_name.strip():
        return {"status": "unknown", "member_id": None}
    target = member_name.casefold().strip()
    named = [member for member in known_members if isinstance(member, dict)]
    exact = [member for member in named if str(member.get("name", "")).casefold().strip() == target]
    if len(exact) == 1:
        return {"status": "matched", "member_id": exact[0].get("id")}
    if len(exact) > 1:
        return {"status": "ambiguous", "member_id": None}
    first_word = [
        member
        for member in named
        if str(member.get("name", "")).casefold().split()[:1] == [target]
    ]
    if len(first_word) == 1:
        return {"status": "matched", "member_id": first_word[0].get("id")}
    if len(first_word) > 1:
        return {"status": "ambiguous", "member_id": None}
    return {"status": "unknown", "member_id": None}


def _is_local_url(url: str) -> bool:
    return url.startswith("http://127.0.0.1") or url.startswith("http://localhost")


def _unavailable_message(exc: BaseException, detail: str = "") -> str:
    reason = str(exc).strip() or exc.__class__.__name__
    extra = f" {detail.strip()}" if detail.strip() else ""
    return (
        f"The local assistant is unavailable ({reason}.{extra}). "
        "Enter payroll values manually."
    )
