"""Safeguard tests for local payroll-note extraction. No live model is called."""

import logging
import threading
import time
from decimal import Decimal

import pytest

from app.ai.extractor import (
    ExtractionError,
    ModelUnavailableError,
    OLLAMA_RESPONSE_FORMAT,
    extract_payroll_note,
    num_predict_for_members,
    ollama_client,
    schedule_warmup,
    warmup_for_monthly_run,
)
from app.ai.prompts import build_extraction_prompt
from app.services.payroll_validation import validate_payroll_inputs


def _client(payload):
    def respond(model_request):
        respond.last_request = model_request
        return payload

    return respond


def test_prompt_uses_english_template_and_held_out_examples():
    prompt = build_extraction_prompt("Anita: 1 extra day.", known_names=["Raj", "Anita", "Dev", "Sam"])

    assert "Known members: Raj, Anita, Dev, Sam" in prompt
    assert "Anita: 1 extra day." in prompt
    assert "Neha" in prompt
    assert "Arjun" in prompt
    assert "Priya" in prompt
    assert "Karan" in prompt
    assert "half day" in prompt
    assert "5k" in prompt
    assert "1.5k" in prompt
    assert "teen din chutti" not in prompt
    assert '"member_name":"Raj"' not in prompt
    assert '"member_name":"Anita"' not in prompt
    assert '"member_name":"Dev"' not in prompt
    assert '"member_name":"Sam"' not in prompt


def test_valid_extraction_requires_human_review():
    client = _client('{"member_name": "Raj Kumar", "total_leaves": 3, "extra_work_days": "2"}')
    result = extract_payroll_note("Raj Kumar teen din chutti", model_client=client, known_members=[{"id": "m1", "name": "Raj Kumar"}])

    assert client.last_request["model"] == "gemma:2b"
    assert client.last_request["temperature"] == 0
    assert client.last_request["member_count"] == 1
    assert result["fields"]["total_leaves"] == Decimal("3")
    assert result["fields"]["extra_work_days"] == Decimal("2")
    assert result["member_match"] == "matched"
    assert result["member_id"] == "m1"
    assert result["requires_human_review"] is True
    assert result["saved"] is False
    assert result["submitted"] is False
    assert result["original_note"] == "Raj Kumar teen din chutti"
    for forbidden in ("tds", "gross_payable", "salary", "approved", "invoice_number", "actual_advance_recovery"):
        assert forbidden not in result
        assert forbidden not in result["fields"]


def test_hinglish_fixture_values_are_kept_when_the_model_returns_them():
    cases = [
        ("teen din chutti", '{"total_leaves": 3}', "total_leaves", Decimal("3")),
        ("aadha din", '{"total_leaves": "0.5"}', "total_leaves", Decimal("0.5")),
        ("paanch hazaar", '{"advance_amount": 5000}', "advance_amount", Decimal("5000")),
        ("10k", '{"advance_amount": "10000"}', "advance_amount", Decimal("10000")),
    ]

    for note, payload, field_name, expected in cases:
        result = extract_payroll_note(note, model_client=_client(payload))
        assert result["fields"][field_name] == expected
        assert result["requires_human_review"] is True


def test_malformed_json_is_rejected():
    with pytest.raises(ExtractionError, match="malformed JSON"):
        extract_payroll_note("3 leaves", model_client=_client("this is not json"))


def test_model_failure_is_a_safe_error():
    def fail(_request):
        raise RuntimeError("boom")

    with pytest.raises(ExtractionError, match="local model failed"):
        extract_payroll_note("3 leaves", model_client=fail)


def test_ambiguous_name_is_not_assigned():
    result = extract_payroll_note(
        "Amit 1 leave",
        model_client=_client('{"member_name": "Amit Shah", "total_leaves": 1}'),
        known_members=[
            {"id": "m1", "name": "Amit Shah"},
            {"id": "m2", "name": "Amit Shah"},
        ],
    )

    assert result["member_match"] == "ambiguous"
    assert result["member_id"] is None


def test_unknown_member_is_not_assigned():
    result = extract_payroll_note(
        "Unknown Person 1 leave",
        model_client=_client('{"member_name": "Unknown Person", "total_leaves": 1}'),
        known_members=[{"id": "m1", "name": "Raj Kumar"}],
    )

    assert result["members"] == []
    assert result["fields"] == {}
    assert result["member_match"] == "unassigned"
    assert result["member_id"] is None
    assert result["unmatched_names"] == ["Unknown Person"]


def test_unique_first_name_matches_the_saved_member():
    result = extract_payroll_note(
        "Raj 3 leaves",
        model_client=_client('{"member_name": "Raj", "total_leaves": 3}'),
        known_members=[{"id": "m1", "name": "Raj Kumar"}],
    )

    assert result["member_match"] == "matched"
    assert result["member_id"] == "m1"
    assert result["members"][0]["member_name"] == "Raj Kumar"
    assert result["unmatched_names"] == []


def test_shared_first_name_is_not_assigned():
    result = extract_payroll_note(
        "Raj 1 leave",
        model_client=_client('{"member_name": "Raj", "total_leaves": 1}'),
        known_members=[
            {"id": "m1", "name": "Raj Kumar"},
            {"id": "m2", "name": "Raj Singh"},
        ],
    )

    assert result["member_match"] == "ambiguous"
    assert result["member_id"] is None
    assert result["unmatched_names"] == ["Raj"]


def test_invalid_day_value_is_reported_by_payroll_validation():
    result = extract_payroll_note(
        "1.3 leaves",
        model_client=_client('{"total_leaves": "1.3"}'),
    )

    assert any("0.5 day increments" in error for error in result["validation_errors"])
    assert result["requires_human_review"] is True
    assert result["saved"] is False


def test_calculated_fields_from_the_model_are_dropped():
    payload = '{"total_leaves": 1, "tds": "6300", "gross_payable": "63000", "approved": true, "invoice_number": "RAKU_INV202609"}'
    result = extract_payroll_note("1 leave", model_client=_client(payload))

    assert result["fields"] == {"total_leaves": Decimal("1")}
    assert "tds" not in result["fields"]
    assert result["saved"] is False


def test_unavailable_model_does_not_block_manual_validation():
    def unavailable(_request):
        raise ModelUnavailableError("local model is unavailable")

    with pytest.raises(ModelUnavailableError):
        extract_payroll_note("1 leave", model_client=unavailable)

    manual = validate_payroll_inputs(
        pay_basis="monthly",
        monthly_payment=Decimal("60000"),
        total_leaves=Decimal("1"),
    )
    assert manual["total_leaves"] == Decimal("1")


def test_external_ollama_url_is_rejected(monkeypatch):
    monkeypatch.setenv("CHILLPAY_OLLAMA_URL", "https://example.com")

    with pytest.raises(ModelUnavailableError, match="local machine"):
        ollama_client({"model": "gemma:2b", "prompt": "note", "temperature": 0})


def _disable_warmup(monkeypatch):
    monkeypatch.setattr("app.ai.extractor.WARMUP_ENABLED", False)


def test_connect_timeout_is_not_running(monkeypatch):
    from app.ai.extractor import ConnectTimeoutError

    _disable_warmup(monkeypatch)
    monkeypatch.setenv("CHILLPAY_OLLAMA_URL", "http://127.0.0.1:11434")

    def http(method, path, body=None, **kwargs):
        raise ConnectTimeoutError("connect timed out")

    monkeypatch.setattr("app.ai.extractor._http_json", http)
    with pytest.raises(ModelUnavailableError, match="not running"):
        ollama_client({"model": "gemma:2b", "prompt": "note", "temperature": 0})


def test_tags_timeout_is_not_running(monkeypatch):
    from app.ai.extractor import ReadTimeoutError

    _disable_warmup(monkeypatch)
    monkeypatch.setenv("CHILLPAY_OLLAMA_URL", "http://127.0.0.1:11434")
    calls = []

    def http(method, path, body=None, **kwargs):
        calls.append(path)
        if path == "/api/tags":
            raise ReadTimeoutError("read timed out")
        raise AssertionError("generate must not run when tags fail")

    monkeypatch.setattr("app.ai.extractor._http_json", http)
    with pytest.raises(ModelUnavailableError, match="not running"):
        ollama_client({"model": "gemma:2b", "prompt": "note", "temperature": 0})
    assert calls == ["/api/tags"]


def test_generate_read_timeout_after_tags_is_model_loading(monkeypatch):
    from app.ai.extractor import ReadTimeoutError

    _disable_warmup(monkeypatch)
    monkeypatch.setenv("CHILLPAY_OLLAMA_URL", "http://127.0.0.1:11434")

    def http(method, path, body=None, **kwargs):
        if path == "/api/tags":
            return {"models": [{"name": "gemma:2b"}]}
        raise ReadTimeoutError("read timed out")

    monkeypatch.setattr("app.ai.extractor._http_json", http)
    with pytest.raises(ModelUnavailableError, match="still loading"):
        ollama_client({"model": "gemma:2b", "prompt": "note", "temperature": 0})


def test_generate_payload_includes_keep_alive_and_num_predict(monkeypatch):
    _disable_warmup(monkeypatch)
    monkeypatch.setenv("CHILLPAY_OLLAMA_URL", "http://127.0.0.1:11434")
    captured = {}

    def http(method, path, body=None, **kwargs):
        if path == "/api/tags":
            return {"models": [{"name": "gemma:2b"}]}
        captured["body"] = body
        return {"response": '{"total_leaves": 1}'}

    monkeypatch.setattr("app.ai.extractor._http_json", http)
    text = ollama_client({"model": "gemma:2b", "prompt": "note", "temperature": 0})
    assert text == '{"total_leaves": 1}'
    assert captured["body"]["keep_alive"] == "30m"
    assert captured["body"]["options"]["num_predict"] == 256
    assert captured["body"]["options"]["temperature"] == 0
    assert captured["body"]["stream"] is False
    assert captured["body"]["format"] == OLLAMA_RESPONSE_FORMAT


def test_keep_alive_and_read_timeout_are_configurable(monkeypatch):
    _disable_warmup(monkeypatch)
    monkeypatch.setenv("CHILLPAY_OLLAMA_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("CHILLPAY_OLLAMA_KEEP_ALIVE", "10m")
    monkeypatch.setenv("CHILLPAY_OLLAMA_READ_TIMEOUT_SECONDS", "45")
    seen = {}

    def http(method, path, body=None, **kwargs):
        if path == "/api/tags":
            return {"models": []}
        seen["keep_alive"] = body["keep_alive"]
        seen["read_timeout"] = kwargs["read_timeout"]
        return {"response": '{"total_leaves": 1}'}

    monkeypatch.setattr("app.ai.extractor._http_json", http)
    ollama_client({"model": "gemma:2b", "prompt": "note", "temperature": 0, "member_count": 1})
    assert seen["keep_alive"] == "10m"
    assert seen["read_timeout"] == 45


def test_num_predict_scales_with_member_count():
    assert num_predict_for_members(0) == 256
    assert num_predict_for_members(1) == 256
    assert num_predict_for_members(3) == 300
    assert num_predict_for_members(20) == 1500


def test_generate_num_predict_uses_member_count(monkeypatch):
    _disable_warmup(monkeypatch)
    monkeypatch.setenv("CHILLPAY_OLLAMA_URL", "http://127.0.0.1:11434")
    captured = {}

    def http(method, path, body=None, **kwargs):
        if path == "/api/tags":
            return {"models": []}
        captured["num_predict"] = body["options"]["num_predict"]
        return {"response": '{"total_leaves": 1}'}

    monkeypatch.setattr("app.ai.extractor._http_json", http)
    ollama_client({"model": "gemma:2b", "prompt": "note", "temperature": 0, "member_count": 5})
    assert captured["num_predict"] == 500


def test_done_reason_length_is_a_cutoff_error(monkeypatch):
    _disable_warmup(monkeypatch)
    monkeypatch.setenv("CHILLPAY_OLLAMA_URL", "http://127.0.0.1:11434")

    def http(method, path, body=None, **kwargs):
        if path == "/api/tags":
            return {"models": []}
        return {"response": '{"total_leaves":', "done_reason": "length"}

    monkeypatch.setattr("app.ai.extractor._http_json", http)
    with pytest.raises(ExtractionError, match="cut off"):
        ollama_client({"model": "gemma:2b", "prompt": "note", "temperature": 0})


def test_warmup_does_not_block_requests(monkeypatch):
    monkeypatch.setattr("app.ai.extractor.WARMUP_ENABLED", True)
    monkeypatch.setattr("app.ai.extractor._warmed_up", False)
    monkeypatch.setattr("app.ai.extractor._warmup_thread", None)
    entered = threading.Event()
    release = threading.Event()

    def stuck_warmup():
        entered.set()
        release.wait(timeout=5)

    monkeypatch.setattr("app.ai.extractor._run_warmup", stuck_warmup)
    started = time.perf_counter()
    schedule_warmup()
    warmup_for_monthly_run()
    assert time.perf_counter() - started < 0.25
    assert entered.wait(timeout=1)

    def http(method, path, body=None, **kwargs):
        if path == "/api/tags":
            return {"models": []}
        return {"response": '{"total_leaves": 1}'}

    monkeypatch.setattr("app.ai.extractor._http_json", http)
    monkeypatch.setenv("CHILLPAY_OLLAMA_URL", "http://127.0.0.1:11434")
    started = time.perf_counter()
    text = ollama_client({"model": "gemma:2b", "prompt": "note", "temperature": 0})
    assert time.perf_counter() - started < 0.25
    assert text == '{"total_leaves": 1}'
    release.set()


def test_timing_log_omits_notes_names_and_amounts(monkeypatch, caplog):
    class FakeSock:
        def settimeout(self, _value):
            return None

    class FakeResponse:
        status = 200

        def read(self):
            return b'{"response":"ok"}'

    class FakeConn:
        sock = FakeSock()

        def __init__(self, host, port, timeout=None):
            self.host = host
            self.port = port

        def connect(self):
            return None

        def request(self, method, path, body=None, headers=None):
            return None

        def getresponse(self):
            return FakeResponse()

        def close(self):
            return None

    monkeypatch.setenv("CHILLPAY_OLLAMA_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr("app.ai.extractor.http.client.HTTPConnection", FakeConn)
    caplog.set_level(logging.WARNING, logger="app.ai.extractor")
    from app.ai.extractor import _http_json

    _http_json(
        "POST",
        "/api/generate",
        {"model": "gemma:2b", "prompt": "Raj Kumar teen din chutti amount 98765"},
        connect_timeout=2,
        read_timeout=90,
    )
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "Raj Kumar" not in joined
    assert "98765" not in joined
    assert "chutti" not in joined
    assert "127.0.0.1" in joined
    assert "11434" in joined
    assert "/api/generate" in joined


def test_unknown_names_are_dropped():
    payload = (
        '{"members":['
        '{"member_name":"Stranger","evidence":"x","total_leaves":9,"extra_work_days":0,'
        '"comp_off":0,"hours_worked":0,"overtime_hours":0,"one_off_bonus":0,"advance_amount":0},'
        '{"member_name":"Raj","evidence":"2 leaves","total_leaves":2,"extra_work_days":0,'
        '"comp_off":0,"hours_worked":0,"overtime_hours":0,"one_off_bonus":0,"advance_amount":0}'
        "]}"
    )
    result = extract_payroll_note(
        "Raj 2 leaves",
        model_client=_client(payload),
        known_members=[{"id": "1", "name": "Raj"}],
    )
    assert [row["member_name"] for row in result["members"]] == ["Raj"]
    assert result["fields"]["total_leaves"] == Decimal("2")
    assert result["evidence"] == "2 leaves"


def test_negatives_are_clamped_to_zero():
    payload = (
        '{"members":[{"member_name":"Raj","evidence":"bad","total_leaves":-3,"extra_work_days":-1,'
        '"comp_off":0,"hours_worked":0,"overtime_hours":-4,"one_off_bonus":-9,"advance_amount":0}]}'
    )
    result = extract_payroll_note(
        "Raj",
        model_client=_client(payload),
        known_members=[{"id": "1", "name": "Raj"}],
    )
    member = result["members"][0]
    assert member["total_leaves"] == Decimal("0")
    assert member["extra_work_days"] == Decimal("0")
    assert member["overtime_hours"] == Decimal("0")
    assert member["one_off_bonus"] == Decimal("0")


def test_non_numeric_values_become_zero():
    payload = (
        '{"members":[{"member_name":"Raj","evidence":"n/a","total_leaves":"abc","extra_work_days":null,'
        '"comp_off":true,"hours_worked":"","overtime_hours":0,"one_off_bonus":"10k","advance_amount":5}]}'
    )
    result = extract_payroll_note(
        "Raj",
        model_client=_client(payload),
        known_members=[{"id": "1", "name": "Raj"}],
    )
    member = result["members"][0]
    assert member["total_leaves"] == Decimal("0")
    assert member["extra_work_days"] == Decimal("0")
    assert member["comp_off"] == Decimal("0")
    assert member["hours_worked"] == Decimal("0")
    assert member["one_off_bonus"] == Decimal("0")
    assert member["advance_amount"] == Decimal("5")


def test_length_cutoff_returns_clear_error(monkeypatch):
    def cut_off(_request):
        raise ExtractionError("The reply was cut off")

    with pytest.raises(ExtractionError, match="The reply was cut off"):
        extract_payroll_note("Raj 1 leave", model_client=cut_off)


def test_evidence_is_returned():
    payload = (
        '{"members":[{"member_name":"Raj","evidence":"3 leaves","total_leaves":3,"extra_work_days":0,'
        '"comp_off":0,"hours_worked":0,"overtime_hours":0,"one_off_bonus":0,"advance_amount":0}]}'
    )
    result = extract_payroll_note(
        "Raj: 3 leaves",
        model_client=_client(payload),
        known_members=[{"id": "1", "name": "Raj"}],
    )
    assert result["evidence"] == "3 leaves"
    assert result["members"][0]["evidence"] == "3 leaves"
    assert result["fields"]["evidence"] == "3 leaves"
