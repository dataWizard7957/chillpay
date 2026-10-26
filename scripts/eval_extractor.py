"""Score the local Gemma extractor on English notes. Localhost only. No database."""

from __future__ import annotations

import logging
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.disable(logging.CRITICAL)

from app.ai.extractor import NUMBER_FIELDS, extract_payroll_note, last_generate_info, ollama_client  # noqa: E402
from app.config import load_environment, ollama_model_name  # noqa: E402

KNOWN = ["Raj", "Anita", "Dev", "Sam"]
KNOWN_MEMBERS = [{"id": name, "name": name} for name in KNOWN]
MODELS = ("gemma:2b", "gemma3:4b")
CASES = [
    (
        "Raj: 3 leaves, 2 extra days, convert 2 to comp-off.",
        {"Raj": {"total_leaves": 3, "extra_work_days": 2, "comp_off": 2}},
    ),
    (
        "Anita: half day leave, 1 extra day.",
        {"Anita": {"total_leaves": 0.5, "extra_work_days": 1}},
    ),
    (
        "Dev: 160 hours, 10 overtime, bonus 2000.",
        {"Dev": {"hours_worked": 160, "overtime_hours": 10, "one_off_bonus": 2000}},
    ),
    (
        "Sam: advance 5000, 2 leaves.",
        {"Sam": {"advance_amount": 5000, "total_leaves": 2}},
    ),
    (
        "Anita: 1.5 days leave, 3 extra days, convert 1 to comp-off.",
        {"Anita": {"total_leaves": 1.5, "extra_work_days": 3, "comp_off": 1}},
    ),
    (
        "Dev: 120 hours, bonus 10k.",
        {"Dev": {"hours_worked": 120, "one_off_bonus": 10000}},
    ),
    (
        "Sam took an advance of 3000 and 4 leaves.",
        {"Sam": {"advance_amount": 3000, "total_leaves": 4}},
    ),
    (
        "Raj 2 leaves, Sam advance 1500.",
        {"Raj": {"total_leaves": 2}, "Sam": {"advance_amount": 1500}},
    ),
    (
        "Anita: no leaves this month, 2 extra days, bonus 5k.",
        {"Anita": {"extra_work_days": 2, "one_off_bonus": 5000}},
    ),
    (
        "Sam: 1 leave and 0.5 extra day.",
        {"Sam": {"total_leaves": 1, "extra_work_days": 0.5}},
    ),
]


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _actual_members(result: dict) -> dict[str, dict]:
    rows = result.get("members") or []
    mapped = {}
    for row in rows:
        name = str(row.get("member_name") or "").strip()
        if name:
            mapped[name.casefold()] = row
    return mapped


def _score(expected: dict, result: dict) -> tuple[list[str], int, int]:
    actual_map = _actual_members(result)
    diffs = []
    correct = 0
    total = 0
    expected_names = {name.casefold() for name in expected}

    for name, fields in expected.items():
        total += 1
        actual = actual_map.get(name.casefold())
        if actual is None:
            diffs.append(f"{name} expected present got missing")
        else:
            correct += 1
        for field, value in fields.items():
            wanted = _dec(value)
            if wanted == 0:
                continue
            total += 1
            got = _dec(actual.get(field) or 0) if actual else Decimal("0")
            if got == wanted:
                correct += 1
            else:
                diffs.append(f"{name}.{field} expected={wanted} got={got}")

    for name_key, actual in actual_map.items():
        display = actual.get("member_name") or name_key
        exp_fields = {}
        for name, fields in expected.items():
            if name.casefold() == name_key:
                exp_fields = fields
                break
        extra_member = name_key not in expected_names
        if extra_member:
            total += 1
            diffs.append(f"{display} expected absent got present")
        for field in NUMBER_FIELDS:
            got = _dec(actual.get(field) or 0)
            if got == 0:
                continue
            wanted = _dec(exp_fields.get(field, 0))
            if extra_member or wanted == 0:
                total += 1
                diffs.append(f"{display}.{field} expected=0 got={got}")
    return diffs, correct, total


def _warmup() -> None:
    try:
        ollama_client(
            {
                "model": ollama_model_name(),
                "prompt": "ok",
                "temperature": 0,
                "member_count": 0,
            }
        )
    except Exception:
        return


def _run_model(model: str) -> tuple[int, int, int, float]:
    os.environ["CHILLPAY_OLLAMA_MODEL"] = model
    _warmup()
    cases_passed = 0
    fields_correct = 0
    fields_total = 0
    elapsed_total = 0.0
    for index, (note, expected) in enumerate(CASES, start=1):
        started = time.perf_counter()
        try:
            result = extract_payroll_note(
                note,
                model_client=ollama_client,
                known_members=KNOWN_MEMBERS,
            )
            error = None
        except Exception as exc:
            result = {"members": []}
            error = exc
        elapsed = time.perf_counter() - started
        elapsed_total += elapsed
        diffs, correct, total = _score(expected, result)
        if error is not None:
            diffs = [f"{type(error).__name__}"] + diffs
        fields_correct += correct
        fields_total += total
        if diffs:
            print(f"{model} {index} FAIL {'; '.join(diffs)} {elapsed:.3f}s")
            info = last_generate_info()
            print(f"raw: {info.get('response')}")
            print(f"done_reason: {info.get('done_reason')}")
        else:
            cases_passed += 1
            print(f"{model} {index} PASS {elapsed:.3f}s")
    average = elapsed_total / len(CASES) if CASES else 0.0
    return cases_passed, fields_correct, fields_total, average


def main() -> int:
    load_environment()
    rows = []
    for model in MODELS:
        passed, correct, total, average = _run_model(model)
        rows.append((model, passed, correct, total, average))
    print("Model\tCases passed\tFields correct\tAverage seconds per case")
    for model, passed, correct, total, average in rows:
        print(f"{model}\t{passed}/{len(CASES)}\t{correct}/{total}\t{average:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
