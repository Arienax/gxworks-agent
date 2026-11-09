"""Canonical owner for user-fixed low-level PLC constraints.

Fresh natural-language interpretation is intentionally outside this module.
Agent A reports non-authoritative claims and Core applies only already-grounded
structured operations here. Legacy callers may still merge structured snapshots.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping

from plc.device_identity import DEVICE_TOKEN_RE, canonical_device


EXPLICIT_USER_CONSTRAINTS_VERSION = 1
_FIELDS = (
    "required_opcodes",
    "forbidden_opcodes",
    "required_devices",
    "forbidden_devices",
    "instruction_instances",
)


def normalize_explicit_user_constraints(value):
    raw = value if isinstance(value, Mapping) else {}
    result = {
        "schema_version": EXPLICIT_USER_CONSTRAINTS_VERSION,
        "source": "user_request",
        "required_opcodes": [],
        "forbidden_opcodes": [],
        "required_devices": [],
        "forbidden_devices": [],
        "instruction_instances": [],
    }
    for key in ("required_opcodes", "forbidden_opcodes"):
        for item in raw.get(key, []) or []:
            token = str(item or "").strip().upper()
            if token and token not in result[key]:
                result[key].append(token)
    for key in ("required_devices", "forbidden_devices"):
        for item in raw.get(key, []) or []:
            token = canonical_device(str(item or "").strip().upper())
            if isinstance(token, str) and DEVICE_TOKEN_RE.fullmatch(token) and token not in result[key]:
                result[key].append(token)
    seen = set()
    for item in raw.get("instruction_instances", []) or []:
        if not isinstance(item, Mapping):
            continue
        opcode = str(item.get("opcode") or "").strip().upper()
        operands = item.get("operands")
        if not opcode or not isinstance(operands, (list, tuple)):
            continue
        normalized_operands = [str(value).strip() for value in operands]
        if any(not value for value in normalized_operands):
            continue
        marker = (opcode, tuple(normalized_operands))
        if marker in seen:
            continue
        seen.add(marker)
        result["instruction_instances"].append({"opcode": opcode, "operands": normalized_operands})
    return result


def _remove_opcode(result, opcode):
    result["required_opcodes"] = [value for value in result["required_opcodes"] if value != opcode]
    result["forbidden_opcodes"] = [value for value in result["forbidden_opcodes"] if value != opcode]


def _remove_device(result, device):
    result["required_devices"] = [value for value in result["required_devices"] if value != device]
    result["forbidden_devices"] = [value for value in result["forbidden_devices"] if value != device]


def apply_explicit_constraint_operations(previous, operations):
    """Apply already-grounded structured edits without interpreting prose."""
    result = normalize_explicit_user_constraints(previous)
    replaced_instance_opcodes = set()
    for raw in operations or []:
        if not isinstance(raw, Mapping):
            continue
        operation = str(raw.get("operation") or "").strip().casefold()
        kind = str(raw.get("kind") or "").strip().casefold()

        if kind == "category" and operation == "clear":
            category = str(raw.get("value") or "").strip().casefold()
            if category in {"all", "opcodes"}:
                result["required_opcodes"] = []
                result["forbidden_opcodes"] = []
                result["instruction_instances"] = []
            if category in {"all", "devices"}:
                result["required_devices"] = []
                result["forbidden_devices"] = []
            if category == "instruction_instances":
                result["instruction_instances"] = []
            continue

        if kind == "opcode":
            values = [str(value or "").strip().upper() for value in raw.get("values", []) or [] if str(value or "").strip()]
            for opcode in values:
                if operation == "clear":
                    _remove_opcode(result, opcode)
                    result["instruction_instances"] = [item for item in result["instruction_instances"] if item["opcode"] != opcode]
                elif operation == "require":
                    result["forbidden_opcodes"] = [value for value in result["forbidden_opcodes"] if value != opcode]
                    if opcode not in result["required_opcodes"]:
                        result["required_opcodes"].append(opcode)
                elif operation == "forbid":
                    result["required_opcodes"] = [value for value in result["required_opcodes"] if value != opcode]
                    result["instruction_instances"] = [item for item in result["instruction_instances"] if item["opcode"] != opcode]
                    if opcode not in result["forbidden_opcodes"]:
                        result["forbidden_opcodes"].append(opcode)
            continue

        if kind == "device":
            values = [canonical_device(str(value or "").strip().upper()) for value in raw.get("values", []) or []]
            values = [value for value in values if isinstance(value, str) and DEVICE_TOKEN_RE.fullmatch(value)]
            for device in values:
                if operation == "clear":
                    _remove_device(result, device)
                elif operation == "require":
                    result["forbidden_devices"] = [value for value in result["forbidden_devices"] if value != device]
                    if device not in result["required_devices"]:
                        result["required_devices"].append(device)
                elif operation == "forbid":
                    result["required_devices"] = [value for value in result["required_devices"] if value != device]
                    if device not in result["forbidden_devices"]:
                        result["forbidden_devices"].append(device)
            continue

        if kind == "instruction_instance":
            normalized = normalize_explicit_user_constraints({"instruction_instances": [raw.get("instance")]})["instruction_instances"]
            if not normalized:
                continue
            instance = normalized[0]
            if operation == "clear":
                result["instruction_instances"] = [item for item in result["instruction_instances"] if item != instance]
            elif operation == "require":
                opcode = instance["opcode"]
                result["forbidden_opcodes"] = [value for value in result["forbidden_opcodes"] if value != opcode]
                if opcode not in result["required_opcodes"]:
                    result["required_opcodes"].append(opcode)
                # A later request replaces prior calls of this opcode once. All
                # calls specified together in the current request must survive.
                if opcode not in replaced_instance_opcodes:
                    result["instruction_instances"] = [item for item in result["instruction_instances"] if item["opcode"] != opcode]
                    replaced_instance_opcodes.add(opcode)
                result["instruction_instances"].append(copy.deepcopy(instance))

    return normalize_explicit_user_constraints(result)


def merge_explicit_user_constraints(previous, update, *, clear_fields=()):
    """Compatibility merge for already-structured snapshots; no text parsing."""
    result = normalize_explicit_user_constraints(previous)
    for field in clear_fields:
        if field in result and isinstance(result[field], list):
            result[field] = []

    current = normalize_explicit_user_constraints(update)
    operations = []
    for opcode in current["required_opcodes"]:
        operations.append({"operation": "require", "kind": "opcode", "values": [opcode]})
    for opcode in current["forbidden_opcodes"]:
        operations.append({"operation": "forbid", "kind": "opcode", "values": [opcode]})
    for device in current["required_devices"]:
        operations.append({"operation": "require", "kind": "device", "values": [device]})
    for device in current["forbidden_devices"]:
        operations.append({"operation": "forbid", "kind": "device", "values": [device]})
    for instance in current["instruction_instances"]:
        operations.append({"operation": "require", "kind": "instruction_instance", "instance": copy.deepcopy(instance)})
    return apply_explicit_constraint_operations(result, operations)


__all__ = [
    "EXPLICIT_USER_CONSTRAINTS_VERSION",
    "apply_explicit_constraint_operations",
    "merge_explicit_user_constraints",
    "normalize_explicit_user_constraints",
]
