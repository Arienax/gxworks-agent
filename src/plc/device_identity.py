"""One spelling for Mitsubishi device aliases, without renumbering devices.

X001 and X1 denote the same index; X010 becomes X10, NEVER X8. This
representation-only boundary strips leading zeroes, not a base conversion.
CPU/range/writability validation remains the existing PLC validator's job.
Comments, literals, contact types and control topology are not rewritten.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping

DEVICE_PREFIXES = (
    "ER", "SM", "SD", "TS", "TC", "CS", "CC",
    "X", "Y", "M", "S", "T", "C", "D", "R", "V", "Z", "P", "I",
)
_DEVICE_PREFIX_PATTERN = "(?:" + "|".join(DEVICE_PREFIXES) + ")"
DEVICE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])" + _DEVICE_PREFIX_PATTERN
    + r"\d+(?:\.\d+)?(?![A-Za-z0-9_])",
    re.I,
)
_SIMPLE = re.compile("(" + _DEVICE_PREFIX_PATTERN + r")(\d+)", re.I)
_INDEXED = re.compile(r"((?:SM|SD|[XYMDTCS])\d+)([VZ]\d+)", re.I)


def device_tokens(value):
    """Return explicit Mitsubishi device tokens in source order."""
    text = str(value or "")
    return tuple(match.group(0).upper() for match in DEVICE_TOKEN_RE.finditer(text))


def canonical_device(value):
    """Normalize a simple device only; leave malformed/non-device values intact."""
    if not isinstance(value, str):
        return value
    match = _SIMPLE.fullmatch(value.strip())
    if match is None:
        return value
    return match[1].upper() + (match[2].lstrip("0") or "0")


def canonical_operand(value):
    """Handle explicit simple/indexed operands, never numbers, strings or names."""
    if not isinstance(value, str):
        return value
    match = _INDEXED.fullmatch(value.strip())
    if match:
        return canonical_device(match[1]) + canonical_device(match[2])
    return canonical_device(value)


def canonical_expression(value):
    if not isinstance(value, str):
        return value
    # Only whole whitespace-delimited operands. In particular quoted strings,
    # K/H constants, bit-group syntax and unrelated labels stay untouched.
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\S+',
                  lambda m: m[0] if m[0].startswith(('"', "'")) else canonical_operand(m[0]), value)


def canonical_device_map(values):
    """Coalesce aliases using the exporter's established first-declaration rule.

    An explicitly empty declaration stays empty. Conflicting source comments
    remain in the immutable source/response audit; they cannot create multiple
    physical devices or override the first declaration through another alias.
    """
    if not isinstance(values, Mapping):
        return copy.deepcopy(values)
    result = {}
    for key, value in values.items():
        identity = canonical_device(key)
        if identity not in result:
            result[identity] = copy.deepcopy(value)
    return result


def canonical_io_rows(rows):
    """Merge zero-padding aliases, retaining row metadata and the chosen label.

    Exact duplicate rows still reach the draft validator before canonicalization.
    This adapter does not treat different physical addresses as interchangeable.
    """
    if not isinstance(rows, list):
        return copy.deepcopy(rows)
    groups, order = {}, []
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("address"):
            order.append((False, copy.deepcopy(row)))
            continue
        address = canonical_device(row["address"])
        if address not in groups:
            order.append((True, address))
            groups[address] = []
        groups[address].append(row)
    result = []
    for grouped, entry in order:
        if not grouped:
            result.append(entry)
            continue
        candidates = sorted(groups[entry], key=lambda r: (
            str(r.get("source", "")).casefold() not in {"user", "confirmed"},
            str(r["address"]).strip().upper() != entry,
            not bool(r.get("label")), str(r["address"]),
        ))
        merged = copy.deepcopy(dict(candidates[0]))
        for row in candidates[1:]:
            for key, value in row.items():
                merged.setdefault(key, copy.deepcopy(value))
        merged["address"] = entry
        result.append(merged)
    return result


def canonical_ladder_devices(ladder):
    """Return a copy with consistent address/comment spelling, including ORs."""
    result = copy.deepcopy(ladder)
    if not isinstance(result, dict):
        return result
    if "device_comments" in result:
        result["device_comments"] = canonical_device_map(result["device_comments"])

    def element(item):
        if not isinstance(item, dict):
            return
        if "address" in item:
            item["address"] = canonical_operand(item["address"])
        if item.get("type") in {"COMPARE", "BLOCK_INPUT", "BLOCK_OUTPUT"} and "expression" in item:
            item["expression"] = canonical_expression(item["expression"])
        if item.get("type") in {"TIMER", "COUNTER"} and "value" in item:
            item["value"] = canonical_operand(item["value"])
        if item.get("type") == "APP_INSTR" and isinstance(item.get("operands"), list):
            item["operands"] = [canonical_operand(v) for v in item["operands"]]
        if item.get("type") == "parallel_block" and isinstance(item.get("branches"), list):
            for branch in item["branches"]:
                if isinstance(branch, list):
                    for child in branch:
                        element(child)

    rows = result.get("rungs")
    for rung in rows if isinstance(rows, list) else ():
        if not isinstance(rung, dict):
            continue
        element(rung.get("header_element"))
        shared = rung.get("shared_inputs")
        for item in shared if isinstance(shared, list) else ():
            element(item)
        branches = rung.get("branches")
        for branch in branches if isinstance(branches, list) else ():
            if not isinstance(branch, dict):
                continue
            for field in ("inputs", "outputs"):
                items = branch.get(field)
                for item in items if isinstance(items, list) else ():
                    element(item)
    return result


def canonical_requirement_devices(requirements):
    """Normalize typed requirement device lists, not their IDs or prose."""
    result = copy.deepcopy(requirements)
    if isinstance(result, (list, tuple)):
        for requirement in result:
            if isinstance(requirement, dict) and isinstance(requirement.get("devices"), list):
                requirement["devices"] = list(dict.fromkeys(canonical_device(v) for v in requirement["devices"]))
    return result


def canonical_analysis_devices(config):
    """Normalize address-bearing fields of an already-normalized analysis config."""
    result = copy.deepcopy(config)
    if not isinstance(result, dict):
        return result
    for pair in result.get("mutex", []) or []:
        if isinstance(pair, dict) and isinstance(pair.get("devices"), list):
            pair["devices"] = [canonical_device(v) for v in pair["devices"]]
    for expectation in result.get("same_scan_expectations", []) or []:
        if isinstance(expectation, dict) and "device" in expectation:
            expectation["device"] = canonical_device(expectation["device"])
    if isinstance(result.get("terminal_states"), Mapping):
        result["terminal_states"] = canonical_device_map(result["terminal_states"])
    return result
