"""Typed simulator signals and JSON scenarios lowered to the existing test DSL.

Word order is low word first, as used by the native D-register interface.
Types describe the observed storage representation, not CPU instruction support.
"""
from __future__ import annotations

import math
import re
import struct
from collections.abc import Mapping

from plc.device_policy import device_address, native_read_plan, native_read_value_kind, native_write_plan
from plc.validation import normalize_plc_model, parse_device_address


FORMATS = {"INT": "h", "UINT": "H", "WORD": "H", "DINT": "i",
           "UDINT": "I", "DWORD": "I", "REAL": "f"}
SIGNAL_TYPES = ("BOOL", *FORMATS, "RAW")


def signal_addresses(address, data_type, plc_model="FX3U", *, writable=False):
    kind = str(data_type).upper()
    if kind not in SIGNAL_TYPES:
        raise ValueError("Unsupported signal type: " + kind)
    access = "stimulus" if writable else "read"
    address = device_address(address, plc_model, access=access, native=True)
    if kind == "RAW":
        if writable:
            raise ValueError("RAW observations are read-only; declare a storage type for inputs")
        return [address]
    if kind == "BOOL":
        if native_read_value_kind(address, plc_model) != "bit":
            raise ValueError("BOOL requires a bit device")
        return [address]
    prefix, index = parse_device_address(address, plc_model)
    if prefix != "D":
        raise ValueError("Typed numeric signals currently require D registers")
    words = struct.calcsize("<" + FORMATS[kind]) // 2
    return [device_address(f"D{index + offset}", plc_model, access=access, native=True)
            for offset in range(words)]


def encode_signal(value, data_type):
    kind = str(data_type).upper()
    if kind == "BOOL":
        if not isinstance(value, (int, bool)) or value not in (0, 1):
            raise ValueError("BOOL requires true, false, 0 or 1")
        return [int(value)]
    if kind not in FORMATS:
        raise ValueError("Unsupported signal type: " + kind)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Numeric signals require a finite JSON number")
    if kind != "REAL" and not isinstance(value, int):
        raise ValueError(kind + " requires an integer")
    try:
        if not math.isfinite(value):
            raise ValueError("Non-finite signal value")
        packed = struct.pack("<" + FORMATS[kind], value)
    except (struct.error, OverflowError) as error:
        raise ValueError("Value outside " + kind + " range") from error
    if kind == "REAL" and not math.isfinite(struct.unpack("<f", packed)[0]):
        raise ValueError("Value outside REAL range")
    return list(struct.unpack("<" + "H" * (len(packed) // 2), packed))


def decode_signal(words, data_type):
    kind = str(data_type).upper()
    if kind == "RAW":
        if len(words) != 1 or type(words[0]) is not int or not -(2**31) <= words[0] < 2**31:
            raise ValueError("Invalid native integer observation")
        return words[0]
    if kind == "BOOL":
        if len(words) != 1 or type(words[0]) is not int or words[0] not in (0, 1):
            raise ValueError("Invalid BOOL observation")
        return bool(words[0])
    if kind not in FORMATS:
        raise ValueError("Unsupported signal type: " + kind)
    count = struct.calcsize("<" + FORMATS[kind]) // 2
    if len(words) != count or any(type(v) is not int or not -32768 <= v <= 65535 for v in words):
        raise ValueError("Incomplete or invalid 16-bit word observation")
    raw = struct.pack("<" + "H" * count, *(v & 0xffff for v in words))
    value = struct.unpack("<" + FORMATS[kind], raw)[0]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite REAL observation")
    return value


def normalize_signals(signals, plc_model="FX3U"):
    from .models import MAX_TRACE_DEVICES
    if not isinstance(signals, list) or not 1 <= len(signals) <= MAX_TRACE_DEVICES:
        raise ValueError(f"A view requires 1 to {MAX_TRACE_DEVICES} signals")
    result, identifiers, word_keys = [], set(), set()
    for index, item in enumerate(signals):
        if not isinstance(item, Mapping) or set(item) - {"id", "label", "address", "data_type", "role", "unit"}:
            raise ValueError(f"signals[{index}]: unknown or invalid fields")
        key = str(item.get("id") or item.get("address") or "")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,95}", key) or key in identifiers:
            raise ValueError("Signal ids must be unique identifiers")
        kind = str(item.get("data_type") or "").upper()
        addresses = signal_addresses(item.get("address"), kind, plc_model)
        role = item.get("role", "state")
        if role not in {"input", "output", "state"}:
            raise ValueError("Signal role must be input, output or state")
        if role == "input":
            signal_addresses(addresses[0], kind, plc_model, writable=True)
        label, unit = str(item.get("label") or key), str(item.get("unit") or "")
        if len(label) > 160 or len(unit) > 24:
            raise ValueError("Signal label or unit too long")
        identifiers.add(key)
        word_keys.update(addresses)
        result.append({"id": key, "label": label, "address": addresses[0], "data_type": kind,
                       "role": role, "unit": unit, "addresses": addresses})
    if len(word_keys) > MAX_TRACE_DEVICES:
        raise ValueError(f"A view may read at most {MAX_TRACE_DEVICES} physical devices per sample")
    native_read_plan(sorted(word_keys), plc_model, maximum=MAX_TRACE_DEVICES)
    return result


def decode_values(signals, raw, *, case=None):
    values, errors = {}, {}
    for signal in signals:
        if case is not None and signal.get("cases") and case not in signal["cases"]:
            values[signal["id"]] = None
            continue
        try:
            values[signal["id"]] = decode_signal([raw.get(a) for a in signal["addresses"]], signal["data_type"])
        except ValueError as error:
            values[signal["id"]] = None
            errors[signal["id"]] = str(error)
    return values, errors


def suite_view_config(suite):
    """Derive a display projection from existing DSL traces; no new PLC model."""
    model = suite["plc_model"]
    signals, keyed = [], {}
    for test in suite["tests"]:
        declared = (test.get("metadata") or {}).get("visualization")
        if declared:
            raw_signals = [{k: s[k] for k in ("id", "label", "address", "data_type", "role", "unit") if k in s}
                           for s in declared.get("signals", [])]
            candidates = normalize_signals(raw_signals, model)
        else:
            candidates = []
            inputs = set(test["initial"])
            for address in test["trace_devices"]:
                value_kind = native_read_value_kind(address, model)
                kind = "BOOL" if value_kind == "bit" else "WORD" if address.startswith("D") else "RAW"
                candidates.append({"id": address, "label": address, "address": address, "addresses": [address],
                                   "data_type": kind, "role": "input" if address in inputs else "state", "unit": ""})
        for signal in candidates:
            identity = (signal["id"], signal["address"], signal["data_type"])
            if identity not in keyed:
                signal = dict(signal)
                if any(s["id"] == signal["id"] for s in signals):
                    signal["id"] = f"case_{len(signals)}.{signal['id']}"
                signal["cases"] = []
                keyed[identity] = signal
                signals.append(signal)
            keyed[identity]["cases"].append(test["name"])
    return {"title": suite["name"], "plc_model": model, "signals": signals,
            "duration_ms": sum(test["timeout_ms"] for test in suite["tests"]),
            "sample_ms": min(test["sample_ms"] for test in suite["tests"]), "events": []}


def normalize_scenario(config):
    """Accept signal names/types/values once; produce an ordinary executable DSL."""
    from simulator.models import normalize_test_suite

    if not isinstance(config, Mapping) or set(config) - {"title", "plc_model", "signals", "initial", "steps", "duration_ms", "sample_ms"}:
        raise ValueError("Invalid simulator view configuration")
    model = normalize_plc_model(config.get("plc_model") or "FX3U")
    signals = normalize_signals(config.get("signals"), model)
    title = str(config.get("title") or "仿真观察")
    if not 1 <= len(title) <= 160:
        raise ValueError("Invalid view title")
    def interval(key, default, lo, hi):
        value = config.get(key, default)
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError(f"{key} must be an integer in {lo}..{hi}")
        return value
    duration = interval("duration_ms", 10000, 50, 300000)
    sample = interval("sample_ms", 100, 50, 5000)
    if duration // sample + 1 > 6001:
        raise ValueError("Too many samples")
    steps = config.get("steps", [])
    if not isinstance(steps, list) or len(steps) > 500:
        raise ValueError("At most 500 steps are allowed")
    by_id = {s["id"]: s for s in signals}
    initial = {}
    raw_initial = config.get("initial", {})
    if not isinstance(raw_initial, Mapping):
        raise ValueError("initial must map signal ids to values")
    for key, value in raw_initial.items():
        if key not in by_id or by_id[key]["role"] != "input":
            raise ValueError("Initial values require a declared input signal: " + key)
        signal = by_id[key]
        if {parse_device_address(a, model) for a in initial}.intersection(parse_device_address(a, model) for a in signal["addresses"]):
            raise ValueError("Initial values overlap physical storage")
        initial.update(zip(signal["addresses"], encode_signal(value, signal["data_type"])))
    lowered, events = [], []
    previous = -1
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping) or set(step) - {"at_ms", "label", "set", "expect"}:
            raise ValueError("Invalid scenario step")
        at = step.get("at_ms")
        if type(at) is not int or not 0 <= at <= duration or at < previous:
            raise ValueError("Step times must be ordered and within duration_ms")
        previous = at
        writes, occupied, expectations = {}, set(), []
        if not isinstance(step.get("set", {}), Mapping) or not isinstance(step.get("expect", {}), Mapping):
            raise ValueError("set and expect must map signal ids to values")
        for key, value in step.get("set", {}).items():
            if key not in by_id or by_id[key]["role"] != "input":
                raise ValueError("Only declared input signals may be written: " + key)
            signal = by_id[key]
            addresses = signal_addresses(signal["address"], signal["data_type"], model, writable=True)
            identities = {parse_device_address(a, model) for a in addresses}
            if occupied.intersection(identities):
                raise ValueError("Scenario writes overlap physical storage")
            occupied.update(identities)
            writes.update(zip(addresses, encode_signal(value, signal["data_type"])))
        native_write_plan(writes, model, allow_empty=True)
        for address in writes:
            if address not in initial:
                raise ValueError("initial must define every written signal before running a scenario")
        for key, expected in step.get("expect", {}).items():
            if key not in by_id:
                raise ValueError("Unknown expected signal: " + key)
            signal = by_id[key]
            tolerance = 0
            if isinstance(expected, Mapping):
                if set(expected) - {"value", "tolerance"}:
                    raise ValueError("Expected values support value and tolerance")
                tolerance = expected.get("tolerance", 0)
                expected = expected.get("value")
            encode_signal(expected, signal["data_type"])
            if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError("Tolerance must be finite and non-negative")
            expectations.append({"address": signal["address"], "data_type": signal["data_type"],
                                 "operator": "eq", "value": expected, "tolerance": tolerance})
        label = str(step.get("label") or f"步骤 {index + 1}")
        lowered.append({"id": f"step_{index + 1}", "display_name": label[:160], "at_ms": at,
                        "set": writes, "expect": expectations, "sample": not (writes or expectations)})
        events.append({"at_ms": at, "label": label[:160], "set": dict(step.get("set") or {}),
                       "expect": dict(step.get("expect") or {}), "kind": "planned"})
    if not lowered or lowered[-1]["at_ms"] < duration:
        lowered.append({"id": "end", "at_ms": duration, "sample": True})
    normalized = {"title": title, "plc_model": model, "signals": signals, "duration_ms": duration,
                  "sample_ms": sample, "events": events}
    suite = normalize_test_suite({"name": title, "plc_model": model, "tests": [{
        "name": title, "plc_model": model, "initial": initial, "steps": lowered, "sample_ms": sample,
        "timeout_ms": duration, "trace_devices": sorted({a for s in signals for a in s["addresses"]}),
        "metadata": {"visualization": normalized}}]}, plc_model=model)
    return normalized, suite
