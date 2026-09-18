"""Model-independent compact ladder boundary: normalize representation, not logic."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from plc_device_identity import canonical_device_map

PROTOCOL_VERSION = "compact_ladder/1.1"
_MAX_BYTES = 2 * 1024 * 1024
_MAX_NODES = 100000
_MAX_DEPTH = 32


class CompactProtocolError(ValueError):
    """A local, safe-to-classify protocol failure, never a provider failure."""
    def __init__(self, message, *, reason="invalid_ladder_structure"):
        # Do not echo model-controlled keys/opcodes through the UI.
        location = str(message).split(":", 1)[0]
        if re.fullmatch(r"r(?:\[\d+\]|\.(?:h|s|b|i|o|or))*", location):
            self.path = "content." + re.sub(r"\[(\d+)\]", r".\1", location)
        else:
            self.path = "content"
        self.reason = reason
        self.diagnostic_id = hashlib.sha256(str(message).encode("utf-8")).hexdigest()[:16]
        super().__init__("梯形图返回结构不完整或字段类型不符（" + self.path + "）。确认规格已保留。")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CompactProtocolError("duplicate JSON key", reason="invalid_json_object")
        result[key] = value
    return result


def _nonfinite_constant(_value):
    raise ValueError("nonfinite JSON number")


def decode_compact(text):
    raw = str(text or "").strip().lstrip("\ufeff").strip()
    if len(raw.encode("utf-8")) > _MAX_BYTES:
        raise CompactProtocolError("response exceeds size limit")
    # Exactly one outer fence is legacy presentation, not an arbitrary wrapper.
    if raw.startswith("```") and raw.endswith("```") and "\n" in raw:
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object,
                           parse_constant=_nonfinite_constant)
    except CompactProtocolError:
        raise
    except (ValueError, RecursionError) as error:
        raise CompactProtocolError("invalid compact JSON", reason="invalid_json_object") from error
    if not isinstance(value, dict):
        raise CompactProtocolError("compact root must be object", reason="invalid_json_object")
    return value


def normalize_compact(value):
    # Inspect before deepcopy to bound work even for caller-supplied objects.
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > _MAX_NODES or depth > _MAX_DEPTH:
            raise CompactProtocolError("compact nesting or node budget exceeded")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    result, changes = copy.deepcopy(value), []
    if isinstance(result, dict) and isinstance(result.get("r"), list):
        for index, row in enumerate(result["r"]):
            if isinstance(row, dict) and "s" in row and row["s"] is None:
                row["s"] = []
                changes.append({"rule": "optional_shared_inputs_null", "path": f"content.r.{index}.s",
                                "from_type": "null", "to_type": "array"})
    return result, changes


_CONTACT_TYPES = frozenset({"NO", "NC", "P", "F", "RISING", "FALLING"})
_COMPARE_PREFIXES = frozenset({"=", "==", "<>", ">=", "<=", ">", "<"})
_TYPED_OUTPUTS = frozenset({"COIL", "PLS", "PLF", "TIMER", "COUNTER"})


def compact_response_schema():
    """Small transport schema; engineering validity remains in existing validators."""
    simple = {"type": "string", "minLength": 1, "maxLength": 160}
    parallel = {
        "type": "object",
        "properties": {
            "or": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "array", "minItems": 1, "items": copy.deepcopy(simple)},
            }
        },
        "required": ["or"],
        "additionalProperties": False,
    }
    branch = {
        "type": "object",
        "properties": {
            "i": {"type": "array", "items": {"anyOf": [copy.deepcopy(simple), parallel]}},
            "o": {"type": "array", "minItems": 1, "items": copy.deepcopy(simple)},
        },
        "required": ["i", "o"],
        "additionalProperties": False,
    }
    rung = {
        "type": "object",
        "properties": {
            "h": {"type": ["string", "null"], "maxLength": 160},
            "s": {"type": "array", "items": copy.deepcopy(simple)},
            "b": {"type": "array", "minItems": 1, "items": branch},
        },
        "required": ["h", "s", "b"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"r": {"type": "array", "minItems": 1, "items": rung}},
        "required": ["r"],
        "additionalProperties": False,
    }


def _simple_input(value, path):
    if not isinstance(value, str) or not value.strip():
        raise CompactProtocolError(f"{path}: expected compact input string")
    token = value.strip()
    head, *tail = token.split(maxsplit=1)
    kind = head.upper()
    if kind in _CONTACT_TYPES:
        if not tail or not tail[0] or any(char.isspace() for char in tail[0]):
            raise CompactProtocolError(f"{path}: contact requires one address")
        return {"type": kind, "address": tail[0]}
    if kind in _COMPARE_PREFIXES:
        if not tail:
            raise CompactProtocolError(f"{path}: comparison requires two operands")
        return {"type": "COMPARE", "expression": token}
    raise CompactProtocolError(f"{path}: unsupported compact input {head!r}")


def _branch_input(value, path):
    if isinstance(value, str):
        return _simple_input(value, path)
    if not isinstance(value, dict) or set(value) != {"or"}:
        raise CompactProtocolError(f"{path}: input must be a string or one OR block")
    branches = value.get("or")
    if not isinstance(branches, list) or not branches:
        raise CompactProtocolError(f"{path}.or: requires at least one branch")
    decoded = []
    for branch_index, branch in enumerate(branches):
        if not isinstance(branch, list) or not branch:
            raise CompactProtocolError(f"{path}.or[{branch_index}]: requires at least one simple input")
        decoded.append([
            _simple_input(item, f"{path}.or[{branch_index}][{item_index}]")
            for item_index, item in enumerate(branch)
        ])
    return {"type": "parallel_block", "branches": decoded}


def _output(value, path):
    if not isinstance(value, str) or not value.strip():
        raise CompactProtocolError(f"{path}: expected compact output string")
    parts = value.strip().split()
    kind = parts[0].upper()
    if kind in {"COIL", "PLS", "PLF"}:
        if len(parts) != 2:
            raise CompactProtocolError(f"{path}: {kind} requires one address")
        return {"type": kind, "address": parts[1]}
    if kind in {"TIMER", "COUNTER"}:
        if len(parts) != 3:
            raise CompactProtocolError(f"{path}: {kind} requires address and preset")
        return {"type": kind, "address": parts[1], "value": parts[2]}
    if kind == "APP":
        if len(parts) < 2:
            raise CompactProtocolError(f"{path}: APP requires an opcode")
        kind, operands = parts[1].upper(), parts[2:]
    else:
        operands = parts[1:]
    if kind in _TYPED_OUTPUTS:
        raise CompactProtocolError(f"{path}: malformed typed output")
    return {"type": "APP_INSTR", "opcode": kind, "operands": operands}


def _confirmed_comments(projected):
    comments = {}
    for row in projected.get("io_table", []) if isinstance(projected, dict) else []:
        if not isinstance(row, dict):
            continue
        address = str(row.get("address") or "").strip().upper()
        text = str(row.get("label") or row.get("description") or "").strip()
        if address and text:
            comments[address] = text[:64]
    return canonical_device_map(comments)


def expand_compact_ladder(compact, projected=None):
    """Deterministically expand Agent B's short protocol into ladder_v1."""
    compact, _changes = normalize_compact(compact)
    if not isinstance(compact, dict) or set(compact) != {"r"}:
        raise CompactProtocolError("compact ladder must contain only top-level field 'r'")
    rows = compact.get("r")
    if not isinstance(rows, list) or not rows:
        raise CompactProtocolError("compact ladder requires at least one rung")

    rungs = []
    for rung_index, row in enumerate(rows, start=1):
        path = f"r[{rung_index - 1}]"
        if not isinstance(row, dict) or not set(row).issubset({"h", "s", "b"}):
            raise CompactProtocolError(f"{path}: invalid compact rung fields")
        branches = row.get("b")
        if not isinstance(branches, list) or not branches:
            raise CompactProtocolError(f"{path}.b: requires at least one branch")
        shared = row.get("s", [])
        if not isinstance(shared, list):
            raise CompactProtocolError(f"{path}.s: expected list")
        header = row.get("h")
        if header is not None:
            header = _simple_input(header, f"{path}.h")

        expanded_branches = []
        for branch_index, branch in enumerate(branches, start=1):
            branch_path = f"{path}.b[{branch_index - 1}]"
            if not isinstance(branch, dict) or not set(branch).issubset({"i", "o"}):
                raise CompactProtocolError(f"{branch_path}: invalid compact branch fields")
            inputs = branch.get("i", [])
            outputs = branch.get("o")
            if not isinstance(inputs, list):
                raise CompactProtocolError(f"{branch_path}.i: expected list")
            if not isinstance(outputs, list) or not outputs:
                raise CompactProtocolError(f"{branch_path}.o: requires at least one output")
            expanded_branches.append({
                "branch_id": branch_index,
                "y_offset_level": branch_index - 1,
                "inputs": [
                    _branch_input(item, f"{branch_path}.i[{index}]")
                    for index, item in enumerate(inputs)
                ],
                "outputs": [
                    _output(item, f"{branch_path}.o[{index}]")
                    for index, item in enumerate(outputs)
                ],
            })

        rungs.append({
            "rung_id": rung_index,
            "header_element": header,
            "shared_inputs": [
                _simple_input(item, f"{path}.s[{index}]")
                for index, item in enumerate(shared)
            ],
            "branches": expanded_branches,
        })

    return {"device_comments": _confirmed_comments(projected or {}), "rungs": rungs}

