"""Minimal path-addressed repair for rejected ladder JSON."""
from __future__ import annotations

import copy
import hashlib
import json
import re

from application.generation_repair import RepairAssemblyError, candidate_base
from instruction_registry import DEFAULT_INSTRUCTION_REGISTRY, generation_app_instr_mnemonics
from plc_generation_contract import MAX_LABEL_LEN

MODE = "field_patch"
SCHEMA_VERSION = 1


def base_sha256(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _segments(path):
    text = str(path or "").strip()
    if text.startswith("content$"):
        text = text[8:]
    elif text.startswith("$"):
        text = text[1:]
    text = re.sub(r"\[([0-9]+)\]", r".\1", text).strip(".")
    if not text:
        return []
    out = []
    for part in text.split("."):
        if not part or part == "*":
            return None
        out.append(int(part) if part.isdigit() else part)
    return out


def _lookup(root, segments):
    current = root
    for segment in segments:
        if isinstance(segment, int):
            if not isinstance(current, list) or not 0 <= segment < len(current):
                raise KeyError(segment)
            current = current[segment]
        else:
            if not isinstance(current, dict) or segment not in current:
                raise KeyError(segment)
            current = current[segment]
    return current


def _pointer(segments):
    return "/" + "/".join(str(x).replace("~", "~0").replace("/", "~1") for x in segments)


def _value_schema(parent, field, plc_model, reason):
    if field == "opcode" and isinstance(parent, dict) and parent.get("type") == "APP_INSTR":
        operands = parent.get("operands")
        if not isinstance(operands, list):
            return None
        allowed = []
        for mnemonic in generation_app_instr_mnemonics(plc_model):
            spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(mnemonic)
            if (
                spec is not None
                and spec.contract_level == "full"
                and spec.accepts_arity(len(operands))
            ):
                allowed.append(mnemonic)
        return {"type": "string", "enum": sorted(allowed)} if allowed else None
    if field in {"label", "debug_note"} and reason == "field_too_long":
        return {"type": ["string", "null"], "maxLength": MAX_LABEL_LEN}
    if field == "branch_id":
        return {"type": "integer", "minimum": 1}
    if field == "y_offset_level":
        return {"type": "integer", "minimum": 0}
    return None


def plan(base, violations, plc_model="FX3U"):
    """Plan exactly one safe scalar patch; structural cases return None."""
    saved = candidate_base(base)
    rows = [row for row in (violations or []) if isinstance(row, dict)]
    if saved is None or len(rows) != 1:
        return None
    row = rows[0]
    segments = _segments(row.get("path"))
    if not segments or segments[0] != "rungs" or not isinstance(segments[-1], str):
        return None
    try:
        current = _lookup(saved, segments)
        parent = _lookup(saved, segments[:-1])
    except KeyError:
        return None
    rule = _value_schema(parent, segments[-1], str(plc_model or "FX3U").upper(), row.get("reason"))
    if rule is None:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "base_sha256": base_sha256(saved),
        "target": {
            "path": _pointer(segments),
            "diagnostic_path": str(row.get("path") or ""),
            "reason": str(row.get("reason") or "invalid_ladder_structure"),
            "current_value": copy.deepcopy(current),
            "context": copy.deepcopy(parent) if segments[-1] == "opcode" else {"field": segments[-1]},
            "value_schema": copy.deepcopy(rule),
        },
    }


def _pointer_segments(pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
        raise RepairAssemblyError("$.patches[0].path", "repair_scope_violation")
    out = []
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not token:
            raise RepairAssemblyError("$.patches[0].path", "repair_scope_violation")
        out.append(int(token) if token.isdigit() else token)
    return out


def _check_value(value, rule):
    expected = rule.get("type")
    kinds = expected if isinstance(expected, list) else [expected]
    if expected is not None and not any(
        (kind == "string" and isinstance(value, str))
        or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (kind == "null" and value is None)
        for kind in kinds
    ):
        raise RepairAssemblyError("$.patches[0].value", "repair_shape_invalid")
    if "enum" in rule and value not in rule["enum"]:
        raise RepairAssemblyError("$.patches[0].value", "repair_scope_violation")
    if isinstance(value, str) and len(value) > rule.get("maxLength", len(value)):
        raise RepairAssemblyError("$.patches[0].value", "field_too_long")
    if isinstance(value, int) and value < rule.get("minimum", value):
        raise RepairAssemblyError("$.patches[0].value", "repair_shape_invalid")


def apply(base, response, repair_plan):
    """Apply one authorized patch to an immutable baseline."""
    saved = candidate_base(base)
    if saved is None or not isinstance(repair_plan, dict) or repair_plan.get("mode") != MODE:
        raise RepairAssemblyError("$.base_sha256", "repair_base_invalid")
    if base_sha256(saved) != repair_plan.get("base_sha256"):
        raise RepairAssemblyError("$.base_sha256", "repair_base_invalid")
    required = {"schema_version", "mode", "base_sha256", "patches"}
    if not isinstance(response, dict) or set(response) != required:
        raise RepairAssemblyError("$", "repair_shape_invalid")
    if response.get("schema_version") != SCHEMA_VERSION or response.get("mode") != MODE:
        raise RepairAssemblyError("$.mode", "repair_shape_invalid")
    if response.get("base_sha256") != repair_plan.get("base_sha256"):
        raise RepairAssemblyError("$.base_sha256", "repair_base_invalid")
    patches = response.get("patches")
    if not isinstance(patches, list) or len(patches) != 1 or not isinstance(patches[0], dict):
        raise RepairAssemblyError("$.patches", "repair_shape_invalid")
    patch = patches[0]
    if set(patch) != {"path", "value"}:
        raise RepairAssemblyError("$.patches[0]", "repair_shape_invalid")
    target = repair_plan.get("target") or {}
    if patch.get("path") != target.get("path"):
        raise RepairAssemblyError("$.patches[0].path", "repair_scope_violation")
    _check_value(patch.get("value"), target.get("value_schema") or {})
    segments = _pointer_segments(target["path"])
    current = _lookup(saved, segments)
    if current == patch.get("value"):
        raise RepairAssemblyError("$.patches[0].value", "repair_no_progress")
    parent = _lookup(saved, segments[:-1])
    leaf = segments[-1]
    if isinstance(leaf, int):
        parent[leaf] = copy.deepcopy(patch["value"])
    else:
        if not isinstance(parent, dict) or leaf not in parent:
            raise RepairAssemblyError("$.patches[0].path", "repair_scope_violation")
        parent[leaf] = copy.deepcopy(patch["value"])
    return saved
