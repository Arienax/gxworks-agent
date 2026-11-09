"""Core-owned defaults and parsing for human-authored simulation drafts.

This module never executes a test and never repairs an invalid input sequence.
The existing strict DSL validator remains the acceptance authority.
"""
from __future__ import annotations

from copy import deepcopy
import math
from uuid import uuid4

from plc.validation import normalize_plc_model
from .models import (DEFAULT_SAMPLE_MS, DEFAULT_WAIT_TIMEOUT_MS, DEFAULT_POLL_MS,
                     DEFAULT_CASE_TIMEOUT_MS, EXPECTATION_OPERATORS,
                     MAX_TEST_DURATION_MS,
                     TestCaseValidationError)


def editor_metadata(plc_model):
    model = normalize_plc_model(plc_model)
    labels = {"eq": "=", "ne": "≠", "gt": ">", "ge": "≥", "lt": "<", "le": "≤", "between": "范围"}
    return {"empty_suite": {"name": "手工仿真方案", "plc_model": model, "tests": []},
            "default_expectation": {"address": "", "operator": "eq", "value": ""},
            "default_input_value": 0, "wait_timeout_ms": DEFAULT_WAIT_TIMEOUT_MS,
            "poll_ms": DEFAULT_POLL_MS, "minimum_interval_ms": 1,
            "maximum_duration_ms": MAX_TEST_DURATION_MS,
            "operators": [{"value": op, "label": labels[op], "initial_value": "",
                           "placeholder": "最小值,最大值" if op == "between" else "填写期望值"}
                          for op in EXPECTATION_OPERATORS]}


def edit_suite(value, command, plc_model):
    model = normalize_plc_model(plc_model)
    suite = deepcopy(value)
    if command.get("action") == "add_test":
        prefix = command.get("name_prefix", "测试")
        index = len(suite["tests"]) + 1
        names = {test["name"] for test in suite["tests"]}
        name = f"{prefix} {index}"
        while name in names:
            name += " +"
        suite["tests"].append({"name": name, "plc_model": model,
            "initial": {}, "steps": [], "trace_devices": [], "sample_ms": DEFAULT_SAMPLE_MS,
            "timeout_ms": DEFAULT_CASE_TIMEOUT_MS})
    elif command.get("action") == "add_step":
        index = command["test_index"]
        test = suite["tests"][index]
        at = test["steps"][-1]["at_ms"] + 100 if test["steps"] else 0
        test["steps"].append({"id": "step_" + uuid4().hex, "at_ms": at,
                              "set": {}, "expect": [], "wait_for": []})
    else:
        raise TestCaseValidationError("unsupported simulation draft command")
    return suite


def _input_scalar(value):
    if not isinstance(value, str):
        return value
    if not value.strip():
        return value
    try:
        number = float(value)
    except ValueError:
        return value
    if not math.isfinite(number):
        return value
    return int(number) if number.is_integer() else number


def parse_editor_suite(value):
    """Parse textual form controls; preserve timings/order/invariants/metadata."""
    suite = deepcopy(value)
    for test in suite["tests"]:
        for step in test.get("steps", []):
            for field in ("expect", "wait_for"):
                expectations = step.get(field, [])
                # Already-normalized maps remain the strict validator's input.
                if not isinstance(expectations, list):
                    continue
                for expectation in expectations:
                    expected = expectation.get("value")
                    if expectation.get("operator") == "between" and isinstance(expected, str):
                        parts = expected.split(",")
                        expectation["value"] = [_input_scalar(part) for part in parts]
                    elif isinstance(expected, str):
                        expectation["value"] = _input_scalar(expected)
    return suite
