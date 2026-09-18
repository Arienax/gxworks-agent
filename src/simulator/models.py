"""Validated PLC test DSL models.

The DSL is plain JSON so an LLM may propose test cases, but only this module
decides whether a case is executable.  Test generation and device execution are
therefore separate trust boundaries.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from plc.validation import parse_device_address


TEST_DSL_SCHEMA_VERSION = 1
MAX_TESTS_PER_SUITE = 200
MAX_STEPS_PER_TEST = 500
MAX_TRACE_DEVICES = 256
MAX_TEST_DURATION_MS = 300_000
INVARIANT_TYPES = {
    "mutual_exclusion",
    "maximum_on_time",
    "minimum_off_time",
    "sequence_constraint",
    "state_constraint",
}
FAULT_TYPES = {
    "stuck_on",
    "stuck_off",
    "signal_delay",
    "signal_bounce",
    "drop_signal",
}

_SAFE_WRITE_PREFIXES = {"X", "M", "D"}
_READ_ONLY_SPECIAL_RE = re.compile(r"^(?:M8\d{3}|D8\d{3}|SM\d+|SD\d+)$", re.I)


class TestCaseValidationError(ValueError):
    pass


def _fail(path: str, message: str) -> None:
    raise TestCaseValidationError(f"{path}: {message}")


def _device(value: Any, plc_model: str, path: str, *, writable: bool = False) -> str:
    address = str(value or "").strip().upper()
    parsed = parse_device_address(address, plc_model)
    if parsed is None:
        _fail(path, f"invalid {plc_model} device address {address!r}")
    prefix = parsed[0].upper()
    if writable:
        if prefix not in _SAFE_WRITE_PREFIXES:
            _fail(path, f"writes to {prefix} devices are not allowed by the test DSL")
        if _READ_ONLY_SPECIAL_RE.fullmatch(address):
            _fail(path, "CPU-owned special devices cannot be written by a test")
    return address


def _number(value: Any, path: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool):
        _fail(path, "must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError):
        _fail(path, "must be a number")
    if result < minimum:
        _fail(path, f"must be >= {minimum}")
    return result


def _int_ms(value: Any, path: str, *, minimum: int = 0) -> int:
    number = _number(value, path, minimum=float(minimum))
    if int(number) != number:
        _fail(path, "must be an integer number of milliseconds")
    return int(number)


def _scalar(value: Any, path: str) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)) and not isinstance(value, (dict, list)):
        return value
    _fail(path, "must be a scalar PLC value")


def _write_scalar(address: str, value: Any, path: str) -> int:
    if isinstance(value, bool):
        value = int(value)
    if not isinstance(value, int):
        _fail(path, "test inputs must be integer PLC values")
    prefix = re.match(r"^[A-Z]+", address).group(0)
    if prefix in {"X", "M"} and value not in {0, 1}:
        _fail(path, "bit devices accept only 0 or 1")
    if prefix == "D" and not -32768 <= value <= 65535:
        _fail(path, "D device value must fit one 16-bit word")
    return value


def _device_values(
    value: Any,
    plc_model: str,
    path: str,
    *,
    writable: bool,
) -> Dict[str, Any]:
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping):
        _fail(path, "must be an object mapping devices to values")
    result = {}
    for raw_address, raw_value in value.items():
        address = _device(raw_address, plc_model, f"{path}.{raw_address}", writable=writable)
        if address in result:
            _fail(path, f"duplicate address {address}")
        result[address] = (
            _write_scalar(address, raw_value, f"{path}.{raw_address}")
            if writable
            else _scalar(raw_value, f"{path}.{raw_address}")
        )
    return dict(sorted(result.items()))


def _expectation(value: Any, plc_model: str, path: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    address = _device(value.get("address"), plc_model, f"{path}.address")
    operator = str(value.get("operator") or "eq").strip().lower()
    if operator not in {"eq", "ne", "gt", "ge", "lt", "le", "between"}:
        _fail(f"{path}.operator", "unsupported comparison operator")
    result = {"address": address, "operator": operator}
    if operator == "between":
        values = value.get("value")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or len(values) != 2:
            _fail(f"{path}.value", "between requires [minimum, maximum]")
        result["value"] = [
            _scalar(values[0], f"{path}.value[0]"),
            _scalar(values[1], f"{path}.value[1]"),
        ]
    else:
        result["value"] = _scalar(value.get("value"), f"{path}.value")
    if value.get("tolerance") is not None:
        result["tolerance"] = _number(
            value.get("tolerance"), f"{path}.tolerance", minimum=0.0
        )
    return result


def _expectations(value: Any, plc_model: str, path: str) -> List[Dict[str, Any]]:
    if value in (None, {}, []):
        return []
    if isinstance(value, Mapping):
        # Short form: {"Y0": 1, "D0": 10}
        if not any(key in value for key in ("address", "operator", "value")):
            return [
                {
                    "address": _device(address, plc_model, f"{path}.{address}"),
                    "operator": "eq",
                    "value": _scalar(expected, f"{path}.{address}"),
                }
                for address, expected in sorted(value.items())
            ]
        value = [value]
    if not isinstance(value, list):
        _fail(path, "must be a device map or expectation list")
    return [
        _expectation(item, plc_model, f"{path}[{index}]")
        for index, item in enumerate(value)
    ]


def _invariant(value: Any, plc_model: str, path: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    kind = str(value.get("type") or "").strip().lower()
    if kind not in INVARIANT_TYPES:
        _fail(f"{path}.type", f"unsupported invariant {kind!r}")
    result: Dict[str, Any] = {"type": kind}
    if kind == "mutual_exclusion":
        devices = value.get("devices")
        if not isinstance(devices, list) or len(devices) < 2:
            _fail(f"{path}.devices", "requires at least two devices")
        result["devices"] = [
            _device(item, plc_model, f"{path}.devices[{index}]")
            for index, item in enumerate(devices)
        ]
    elif kind in {"maximum_on_time", "minimum_off_time"}:
        result["device"] = _device(value.get("device"), plc_model, f"{path}.device")
        result["duration_ms"] = _int_ms(
            value.get("duration_ms"), f"{path}.duration_ms", minimum=1
        )
    elif kind == "sequence_constraint":
        devices = value.get("devices")
        if not isinstance(devices, list) or len(devices) < 2:
            _fail(f"{path}.devices", "requires at least two ordered devices")
        result["devices"] = [
            _device(item, plc_model, f"{path}.devices[{index}]")
            for index, item in enumerate(devices)
        ]
        result["allow_repeat"] = bool(value.get("allow_repeat", False))
    else:
        result["device"] = _device(value.get("device"), plc_model, f"{path}.device")
        allowed = value.get("allowed")
        if not isinstance(allowed, list) or not allowed:
            _fail(f"{path}.allowed", "requires a non-empty allowed value list")
        result["allowed"] = [
            _scalar(item, f"{path}.allowed[{index}]")
            for index, item in enumerate(allowed)
        ]
    if value.get("name"):
        result["name"] = str(value["name"])[:160]
    return result


def _fault(value: Any, plc_model: str, path: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    kind = str(value.get("type") or "").strip().lower()
    if kind not in FAULT_TYPES:
        _fail(f"{path}.type", f"unsupported fault type {kind!r}")
    result = {
        "type": kind,
        "device": _device(value.get("device"), plc_model, f"{path}.device", writable=True),
        "at_ms": _int_ms(value.get("at_ms", 0), f"{path}.at_ms"),
    }
    if kind == "signal_delay":
        result["delay_ms"] = _int_ms(
            value.get("delay_ms"), f"{path}.delay_ms", minimum=1
        )
    elif kind == "signal_bounce":
        result["duration_ms"] = _int_ms(
            value.get("duration_ms", 20), f"{path}.duration_ms", minimum=1
        )
        result["interval_ms"] = _int_ms(
            value.get("interval_ms", 5), f"{path}.interval_ms", minimum=1
        )
    elif kind == "drop_signal":
        result["duration_ms"] = _int_ms(
            value.get("duration_ms", 0), f"{path}.duration_ms", minimum=0
        )
    return result


def normalize_test_case(value: Any, *, plc_model: str = "FX3U") -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("$", "test case must be an object")
    schema = value.get("schema_version", TEST_DSL_SCHEMA_VERSION)
    if schema != TEST_DSL_SCHEMA_VERSION:
        _fail("$.schema_version", f"unsupported schema version {schema!r}")
    name = str(value.get("name") or "").strip()
    if not name:
        _fail("$.name", "is required")
    model = str(value.get("plc_model") or plc_model or "FX3U").upper()
    initial = _device_values(value.get("initial"), model, "$.initial", writable=True)
    raw_steps = value.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        _fail("$.steps", "requires at least one step")
    if len(raw_steps) > MAX_STEPS_PER_TEST:
        _fail("$.steps", f"supports at most {MAX_STEPS_PER_TEST} steps")
    steps = []
    previous_at = -1
    seen_ids = set()
    for index, raw in enumerate(raw_steps):
        path = f"$.steps[{index}]"
        if not isinstance(raw, Mapping):
            _fail(path, "must be an object")
        at_ms = _int_ms(raw.get("at_ms", 0), f"{path}.at_ms")
        if at_ms < previous_at:
            _fail(f"{path}.at_ms", "steps must be in nondecreasing time order")
        previous_at = at_ms
        step_id = str(raw.get("id") or f"step_{index + 1:04d}").strip()
        if step_id in seen_ids:
            _fail(f"{path}.id", f"duplicate step id {step_id!r}")
        seen_ids.add(step_id)
        set_values = _device_values(raw.get("set"), model, f"{path}.set", writable=True)
        expectations = _expectations(raw.get("expect"), model, f"{path}.expect")
        wait_for = _expectations(raw.get("wait_for"), model, f"{path}.wait_for")
        if not (set_values or expectations or wait_for):
            _fail(path, "requires set, expect, or wait_for")
        step = {
            "id": step_id,
            "at_ms": at_ms,
            "set": set_values,
            "expect": expectations,
            "wait_for": wait_for,
        }
        if wait_for:
            step["timeout_ms"] = _int_ms(
                raw.get("timeout_ms", 1000), f"{path}.timeout_ms", minimum=1
            )
            step["poll_ms"] = _int_ms(
                raw.get("poll_ms", 10), f"{path}.poll_ms", minimum=1
            )
        steps.append(step)

    invariants_raw = value.get("invariants") or []
    if not isinstance(invariants_raw, list):
        _fail("$.invariants", "must be a list")
    invariants = [
        _invariant(item, model, f"$.invariants[{index}]")
        for index, item in enumerate(invariants_raw)
    ]
    faults_raw = value.get("fault_injections") or []
    if not isinstance(faults_raw, list):
        _fail("$.fault_injections", "must be a list")
    faults = [
        _fault(item, model, f"$.fault_injections[{index}]")
        for index, item in enumerate(faults_raw)
    ]
    fault_devices = [item["device"] for item in faults]
    if len(fault_devices) != len(set(fault_devices)):
        _fail("$.fault_injections", "only one fault may target each device")
    trace_devices_raw = value.get("trace_devices") or []
    if not isinstance(trace_devices_raw, list):
        _fail("$.trace_devices", "must be a list")
    trace_devices = {
        _device(item, model, f"$.trace_devices[{index}]")
        for index, item in enumerate(trace_devices_raw)
    }
    trace_devices.update(initial)
    for step in steps:
        trace_devices.update(step["set"])
        trace_devices.update(item["address"] for item in step["expect"])
        trace_devices.update(item["address"] for item in step["wait_for"])
    for invariant in invariants:
        trace_devices.update(invariant.get("devices") or [])
        if invariant.get("device"):
            trace_devices.add(invariant["device"])
    trace_devices.update(fault_devices)
    if len(trace_devices) > MAX_TRACE_DEVICES:
        _fail("$.trace_devices", f"supports at most {MAX_TRACE_DEVICES} unique devices")
    stimulus_devices = set(fault_devices)
    for step in steps:
        stimulus_devices.update(step["set"])
    missing_initial = sorted(stimulus_devices.difference(initial))
    if missing_initial:
        _fail(
            "$.initial",
            "must define every stimulus/fault device: " + ", ".join(missing_initial),
        )
    sample_ms = _int_ms(value.get("sample_ms", 10), "$.sample_ms", minimum=1)
    timeout_ms = _int_ms(
        value.get("timeout_ms", max(item["at_ms"] for item in steps) + 5000),
        "$.timeout_ms",
        minimum=1,
    )
    if timeout_ms < steps[-1]["at_ms"]:
        _fail("$.timeout_ms", "must not be earlier than the final step")
    if timeout_ms > MAX_TEST_DURATION_MS:
        _fail("$.timeout_ms", f"must be <= {MAX_TEST_DURATION_MS}")
    for index, fault in enumerate(faults):
        if fault["at_ms"] > timeout_ms:
            _fail(f"$.fault_injections[{index}].at_ms", "must occur within the test timeout")
    return {
        "schema_version": TEST_DSL_SCHEMA_VERSION,
        "name": name[:200],
        "plc_model": model,
        "description": str(value.get("description") or "")[:1000],
        "initial": initial,
        "steps": steps,
        "invariants": invariants,
        "fault_injections": faults,
        "trace_devices": sorted(trace_devices),
        "sample_ms": sample_ms,
        "timeout_ms": timeout_ms,
        "metadata": copy.deepcopy(value.get("metadata"))
        if isinstance(value.get("metadata"), Mapping)
        else {},
    }


def normalize_test_suite(value: Any, *, plc_model: str = "FX3U") -> Dict[str, Any]:
    if isinstance(value, list):
        value = {"name": "regression", "tests": value}
    if not isinstance(value, Mapping):
        _fail("$", "test suite must be an object or test list")
    raw_tests = value.get("tests")
    if not isinstance(raw_tests, list) or not raw_tests:
        _fail("$.tests", "requires at least one test")
    if len(raw_tests) > MAX_TESTS_PER_SUITE:
        _fail("$.tests", f"supports at most {MAX_TESTS_PER_SUITE} tests")
    model = str(value.get("plc_model") or plc_model or "FX3U").upper()
    tests = [normalize_test_case(item, plc_model=model) for item in raw_tests]
    if any(item["plc_model"] != model for item in tests):
        _fail("$.tests", "all tests must use the suite PLC model")
    names = [item["name"] for item in tests]
    if len(names) != len(set(names)):
        _fail("$.tests", "test names must be unique")
    return {
        "schema_version": TEST_DSL_SCHEMA_VERSION,
        "name": str(value.get("name") or "regression")[:200],
        "plc_model": model,
        "tests": tests,
    }


__all__ = [
    "FAULT_TYPES",
    "INVARIANT_TYPES",
    "TEST_DSL_SCHEMA_VERSION",
    "TestCaseValidationError",
    "normalize_test_case",
    "normalize_test_suite",
]
