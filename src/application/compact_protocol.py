# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Model-independent compact ladder boundary: normalize representation, not logic."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from functools import lru_cache

from jsonschema import Draft202012Validator
from plc.comments import (
    GXWORKS2_DEVICE_COMMENT_MAX_CHARS, confirmed_device_comments,
    normalize_device_comments, normalize_generated_device_comments,
)

LEGACY_PROTOCOL_VERSION = "compact_ladder/1.3"
PROTOCOL_VERSION = "compact_ladder/1.4"
_MAX_BYTES = 2 * 1024 * 1024
_MAX_NODES = 100000
_MAX_DEPTH = 32


class CompactProtocolError(ValueError):
    """A local, safe-to-classify protocol failure, never a provider failure."""
    def __init__(self, message, *, reason="invalid_ladder_structure", schema_keyword=None):
        # Do not echo model-controlled keys/opcodes through the UI.
        location = str(message).split(":", 1)[0]
        if re.fullmatch(r"r(?:\[\d+\]|\.(?:c|h|s|b|i|o|or))*|instruction_edits\[\d+\]\.instructions\[\d+\]", location):
            self.path = "content." + re.sub(r"\[(\d+)\]", r".\1", location)
        else:
            self.path = "content"
        self.reason = reason
        schema_details = {
            "required": "缺少必填字段", "type": "字段类型不符",
            "additionalProperties": "存在未知字段", "minItems": "数组不能为空",
            "minLength": "字符串不能为空", "maxLength": "字符串超过协议长度",
            "anyOf": "输入结构不符合协议",
        }
        self.schema_keyword = schema_keyword if schema_keyword in schema_details else None
        detail = schema_details.get(self.schema_keyword, "结构不完整或字段类型不符")
        self.diagnostic_id = hashlib.sha256(str(message).encode("utf-8")).hexdigest()[:16]
        super().__init__("梯形图返回" + detail + "（" + self.path + "）。确认规格已保留。")


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
    # Direct's documented transport envelope carries exactly one result. Never
    # pick a program out of a mixed response or discard a clarification payload.
    if (isinstance(result, dict) and set(result) == {"result"}
            and isinstance(result["result"], dict) and "r" in result["result"]
            and set(result["result"]) <= {"r", "device_comments"}):
        result = result["result"]
        changes.append({"rule": "direct_result_envelope", "path": "content"})
    # A transport format annotation carries no ladder semantics. Remove only
    # this exact, observed spelling; conflicting/unknown fields still fail.
    if (isinstance(result, dict) and {'r', 'type'} <= set(result) <= {'r', 'type', 'device_comments'}
            and result['type'] == 'json_object' and isinstance(result['r'], list)):
        result.pop('type')
        changes.append({'rule': 'transport_format_annotation', 'path': 'content',
                        'removed_field': 'type', 'value': 'json_object'})
    # One observed field alias, never unwrap arbitrary objects or choose between
    # competing representations. Full schema/PLC checks still run afterwards.
    if (isinstance(result, dict) and "root" in result and set(result) <= {"root", "device_comments"}
            and isinstance(result["root"], list)):
        result["r"] = result.pop("root")
        changes.append({"rule": "root_field_alias", "path": "content.r",
                        "from_field": "root", "to_field": "r"})
    if isinstance(result, dict) and isinstance(result.get("r"), list):
        for index, row in enumerate(result["r"]):
            if not isinstance(row, dict):
                continue
            header = row.get("h")
            if isinstance(header, list) and len(header) == 1 and isinstance(header[0], str):
                row["h"] = header[0]
                changes.append({"rule": "single_header_wrapper", "path": f"content.r.{index}.h",
                                "from_type": "array", "to_type": "string"})
            for field in ("c", "s"):
                if isinstance(row.get(field), str):
                    row[field] = [row[field]]
                    changes.append({"rule": "single_series_string", "path": f"content.r.{index}.{field}",
                                    "from_type": "string", "to_type": "array"})
            if isinstance(row, dict) and "s" in row and row["s"] is None:
                row["s"] = []
                changes.append({"rule": "optional_shared_inputs_null", "path": f"content.r.{index}.s",
                                "from_type": "null", "to_type": "array"})
            if not isinstance(row, dict) or not isinstance(row.get("b"), list):
                continue
            for branch_index, branch in enumerate(row["b"]):
                if not isinstance(branch, dict):
                    continue
                for field in ("i", "o"):
                    if isinstance(branch.get(field), str):
                        branch[field] = [branch[field]]
                        changes.append({"rule": "single_branch_string", "path": f"content.r.{index}.b.{branch_index}.{field}",
                                        "from_type": "string", "to_type": "array"})
                inputs = branch.get("i")
                if (isinstance(inputs, list) and len(inputs) == 1
                        and isinstance(inputs[0], list) and inputs[0]
                        and all(isinstance(item, str) or
                                isinstance(item, dict) and set(item) == {"or"}
                                for item in inputs[0])):
                    branch["i"] = inputs[0]
                    changes.append({"rule": "single_series_input_wrapper",
                                    "path": f"content.r.{index}.b.{branch_index}.i",
                                    "from_type": "wrapped_array", "to_type": "array"})
            # Evaluate the shared parallel guard once. Unconditional terminal
            # coils on separate branches have the same enable and ordered writes.
            common_field = "c" if "c" in row else "s"
            shared = row.get(common_field)
            if (isinstance(shared, list) and any(isinstance(item, dict) and set(item) == {'or'} for item in shared)
                    and len(row['b']) > 1 and all(isinstance(branch, dict) and set(branch) == {'i', 'o'}
                        and branch['i'] == [] and isinstance(branch['o'], list) and branch['o']
                        and all(isinstance(op, str) and op.startswith('COIL ') for op in branch['o'])
                        for branch in row['b'])):
                row['b'] = [{'i': [], 'o': [op for branch in row['b'] for op in branch['o']]}]
                changes.append({'rule': 'shared_parallel_terminal_coils', 'path': f'content.r.{index}.b'})
            if (len(row["b"]) == 1 and isinstance(row["b"][0], dict)
                    and isinstance(row["b"][0].get("i", []), list)
                    and isinstance(shared, list)
                    and any(isinstance(item, dict) and set(item) == {"or"} for item in shared)
                    and all(isinstance(item, str) or isinstance(item, dict) and set(item) in ({"or"}, {"condition_ref", "when"})
                            for item in shared)):
                row["b"][0]["i"] = shared + row["b"][0].get("i", [])
                row[common_field] = []
                changes.append({"rule": "single_branch_shared_parallel", "path": f"content.r.{index}",
                                "from_field": common_field, "to_field": "b.0.i"})
    return result, changes


_CONTACT_TYPES = frozenset({"NO", "NC", "P", "F", "RISING", "FALLING"})
# A bare bit in a Boolean input expression is a positive literal. Other
# device and operand forms still need their explicit contact or comparison.
_BARE_CONTACT_PREFIXES = frozenset({"X", "Y", "M", "SM", "S", "T", "C", "L", "F", "B", "SB",
                                  "ST", "TS", "TC", "CS", "CC", "DX", "DY"})
# Native Mitsubishi instruction-list contact spellings carry the same predicate
# semantics; compact topology owns whether that predicate is first/series/parallel.
_MITSUBISHI_CONTACT_ALIASES = {
    "LD": "NO", "AND": "NO", "OR": "NO",
    "LDI": "NC", "ANI": "NC", "ORI": "NC",
    "LDP": "P", "ANDP": "P", "ORP": "P",
    "LDF": "F", "ANDF": "F", "ORF": "F",
}
_COMPARE_PREFIXES = frozenset({"=", "==", "<>", ">=", "<=", ">", "<"})
_TYPED_OUTPUTS = frozenset({"COIL", "PLS", "PLF", "TIMER", "COUNTER"})


def bound_output_schemas():
    """Named application calls and exact timer durations share both edit paths."""
    simple = {"type": "string", "minLength": 1, "maxLength": 160}
    ticks = {"type": "object", "additionalProperties": False, "required": ["duration_ms", "tick_ms"],
             "properties": {"duration_ms": {"type": "number", "minimum": 0},
                            "tick_ms": {"type": "number", "exclusiveMinimum": 0}}}
    call = {
        "type": "object", "additionalProperties": False, "required": ["form_ref", "parameters"],
        "properties": {"form_ref": simple, "parameters": {
            "type": "array", "maxItems": 32, "items": {"type": "object", "additionalProperties": False,
                "required": ["name", "value"], "properties": {"name": simple, "value": {"anyOf": [simple, ticks]}},
            }}},
    }
    timer = {**ticks, "required": ["timer", *ticks["required"]],
             "properties": {"timer": {"type": "string", "pattern": r"^[Tt]\d+$"}, **ticks["properties"]}}
    return [call, timer]


def compact_response_schema(*, allow_construct=False, legacy=False, partial=False, bindings=True):
    """Small transport schema; engineering validity remains in existing validators."""
    simple = {"type": "string", "minLength": 1, "maxLength": 160}
    contact, output = simple, simple
    if bindings and not legacy:
        condition = {
            "type": "object", "additionalProperties": False, "required": ["condition_ref", "when"],
            "properties": {"condition_ref": {"type": "string", "minLength": 1, "maxLength": 160},
                           "when": {"type": "string", "enum": ["active", "inactive", "run_permit"]}},
        }
        contact, output = {"anyOf": [simple, condition]}, {"anyOf": [simple, *bound_output_schemas()]}
    parallel = {
        "type": "object",
        "properties": {
            "or": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "array", "minItems": 1, "items": copy.deepcopy(contact)},
            }
        },
        "required": ["or"],
        "additionalProperties": False,
    }
    branch = {
        "type": "object",
        "properties": {
            "i": {"type": "array", "items": {"anyOf": [*contact.get("anyOf", [simple]), parallel]}},
            "o": {"type": "array", "minItems": 1, "items": copy.deepcopy(output)},
        },
        "required": ["i", "o"],
        "additionalProperties": False,
    }
    rung = {
        "type": "object",
        "properties": {
            "c": {"type": "array", "items": copy.deepcopy(contact)},
            "b": {"type": "array", "minItems": 1, "items": branch},
        },
        "required": ["c", "b"],
        "additionalProperties": False,
    }
    if legacy:
        rung["properties"].pop("c")
        rung["properties"].update(h={"type": ["string", "null"], "maxLength": 160},
                                  s={"type": "array", "items": copy.deepcopy(simple)})
        rung["required"] = ["h", "s", "b"]
    schema = {
        "type": "object",
        "properties": {
            "r": {"type": "array", "minItems": 1, "items": rung},
            "device_comments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "address": copy.deepcopy(simple),
                        "comment": {"type": "string", "maxLength": GXWORKS2_DEVICE_COMMENT_MAX_CHARS},
                    },
                    "required": ["address", "comment"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["r", "device_comments"],
        "additionalProperties": False,
    }
    if partial:
        rung['properties']['rung_id'] = {'type': 'integer', 'minimum': 0}
        rung['required'].append('rung_id')
        schema['properties']['r']['minItems'] = 0
        schema['properties'].update(
            mode={'type': 'string', 'enum': ['partial']},
            delete_rung_ids={'type': 'array', 'items': {'type': 'integer', 'minimum': 0}})
        schema['required'].extend(['mode', 'delete_rung_ids'])
    elif allow_construct:
        reference = {'type': 'object', 'properties': {'construct': {'type': 'string', 'minLength': 1, 'maxLength': 64}},
                     'required': ['construct'], 'additionalProperties': False}
        schema['properties']['r']['items'] = {'anyOf': [schema['properties']['r']['items'], reference]}
    return schema


@lru_cache(maxsize=3)
def _compact_validator(part="root"):
    # A private snapshot: callers of compact_response_schema cannot mutate it.
    schema = compact_response_schema(legacy=part == "legacy")
    if part == "root":
        # New wire responses carry annotations; older compact replies remain valid.
        schema["required"] = ["r"]
        schema["properties"]["r"]["items"] = {}
    else:
        schema = schema["properties"]["r"]["items"]
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_compact_structure(value):
    """Validate canonical wire structure; do not infer PLC semantics or coerce types."""
    error = next(_compact_validator().iter_errors(value), None)
    prefix = []
    if error is None:
        for index, row in enumerate(value["r"]):
            mode = "current" if isinstance(row, dict) and "c" in row else "legacy"
            error = next(_compact_validator(mode).iter_errors(row), None)
            if error is not None:
                prefix = ["r", index]
                break
        else:
            return
    _raise_schema_error(error, prefix)


def _raise_schema_error(error, prefix=()):
    parts = list(prefix) + list(error.absolute_path)
    if error.validator == "required" and isinstance(error.instance, dict):
        # Report the missing schema-owned field, not model-controlled error text.
        missing = next((key for key in error.validator_value if key not in error.instance), None)
        if missing is not None:
            parts.append(missing)
    path = ""
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else ("." if path else "") + str(part)
    raise CompactProtocolError(f"{path}: schema {error.validator}", schema_keyword=error.validator)


def _materialize_legacy_defaults(value):
    """Keep previously supported omissions before applying the canonical schema.

    h/s/i have always defaulted to null/[]/[] in the decoder. Explicit invalid
    values are not replaced; required branches and outputs are never invented.
    The caller already owns the detached copy made by normalize_compact.
    """
    if not isinstance(value, dict) or not isinstance(value.get("r"), list):
        return
    for row in value["r"]:
        if not isinstance(row, dict):
            continue
        if "c" not in row:
            row.setdefault("h", None)
            row.setdefault("s", [])
        if isinstance(row.get("b"), list):
            for branch in row["b"]:
                if isinstance(branch, dict):
                    branch.setdefault("i", [])


def _simple_input(value, path, bindings=None):
    if bindings is not None and isinstance(value, dict):
        value = bindings.condition(value, path)
    if not isinstance(value, str) or not value.strip():
        raise CompactProtocolError(f"{path}: expected compact input string")
    token = value.strip()
    head, *tail = token.split(maxsplit=1)
    kind = head.upper()
    kind = _MITSUBISHI_CONTACT_ALIASES.get(kind, kind)
    if kind in _CONTACT_TYPES:
        if not tail or not tail[0] or any(char.isspace() for char in tail[0]):
            raise CompactProtocolError(f"{path}: contact requires one address")
        return {"type": kind, "address": tail[0]}
    if kind in _COMPARE_PREFIXES:
        if not tail:
            raise CompactProtocolError(f"{path}: comparison requires two operands")
        return {"type": "COMPARE", "expression": token}
    if not tail:
        from plc.device_identity import reference_device_parts
        device = reference_device_parts(head)
        if device is not None and device[0] in _BARE_CONTACT_PREFIXES:
            return {"type": "NO", "address": head}
    raise CompactProtocolError(f"{path}: unsupported compact input {head!r}")


def _branch_input(value, path, bindings=None):
    if isinstance(value, str) or isinstance(value, dict) and "condition_ref" in value:
        return _simple_input(value, path, bindings)
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
            _simple_input(item, f"{path}.or[{branch_index}][{item_index}]", bindings)
            for item_index, item in enumerate(branch)
        ])
    return {"type": "parallel_block", "branches": decoded}


def _output(value, path, bindings=None):
    if bindings is not None and isinstance(value, dict):
        return bindings.timer(value, path) if 'timer' in value else bindings.call(value, path)
    if not isinstance(value, str) or not value.strip():
        raise CompactProtocolError(f"{path}: expected compact output string")
    parts = value.strip().split()
    kind = parts[0].upper()
    if kind == "OUT":
        if len(parts) == 2:
            kind = "COIL"
        elif len(parts) == 3 and re.fullmatch(r"[TC]\d+", parts[1], re.I):
            kind = "TIMER" if parts[1][0].upper() == "T" else "COUNTER"
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
    return confirmed_device_comments(projected)


def expand_compact_ladder(compact, projected=None, *, plc_model="FX3U", bindings=None):
    """Deterministically expand Agent B's short protocol into ladder_v1."""
    compact, _changes = normalize_compact(compact)
    _materialize_legacy_defaults(compact)
    validate_compact_structure(compact)
    if bindings is None:
        from application.compact_bindings import CompactBindings
        bindings = CompactBindings(plc_model, projected)
    rows = compact["r"]

    rungs = []
    for rung_index, row in enumerate(rows, start=1):
        path = f"r[{rung_index - 1}]"
        branches = row["b"]
        common_field = "c" if "c" in row else "s"
        shared = row[common_field]
        header = row.get("h")
        if header is not None:
            header = _simple_input(header, f"{path}.h")

        expanded_branches = []
        for branch_index, branch in enumerate(branches, start=1):
            branch_path = f"{path}.b[{branch_index - 1}]"
            inputs = branch["i"]
            outputs = branch["o"]
            expanded_branches.append({
                "branch_id": branch_index,
                "y_offset_level": branch_index - 1,
                "inputs": [
                    _branch_input(item, f"{branch_path}.i[{index}]", bindings)
                    for index, item in enumerate(inputs)
                ],
                "outputs": [
                    _output(item, f"{branch_path}.o[{index}]", bindings)
                    for index, item in enumerate(outputs)
                ],
            })

        rungs.append({
            "rung_id": rung_index,
            "header_element": header,
            "shared_inputs": [
                _simple_input(item, f"{path}.{common_field}[{index}]", bindings)
                for index, item in enumerate(shared)
            ],
            "branches": expanded_branches,
        })

    comments = normalize_generated_device_comments(
        {row["address"]: row["comment"] for row in compact.get("device_comments", [])},
        projected, include_confirmed=True,
    )
    return {"device_comments": normalize_device_comments(comments), "rungs": rungs}


def expand_compact_partial(value, projected=None, *, plc_model="FX3U", bindings=None):
    """Expand edit rows without renumbering targets or injecting unedited comments.

    The existing partial merger owns duplicate/deleted identities and edit scope.
    Conditions and actions use exactly the same decoder as fresh generation.
    """
    compact, _changes = normalize_compact(value)
    error = next(Draft202012Validator(compact_response_schema(partial=True)).iter_errors(compact), None)
    if error is not None:
        _raise_schema_error(error)
    rows = compact['r']
    identities = [row.pop('rung_id') for row in rows]
    if rows:
        if bindings is None:
            from application.compact_bindings import CompactBindings
            bindings = CompactBindings(plc_model, projected)
        ladder = expand_compact_ladder({'r': rows, 'device_comments': compact['device_comments']},
                                       plc_model=plc_model, bindings=bindings)
        for identity, rung in zip(identities, ladder['rungs']):
            rung['rung_id'] = identity
    else:
        ladder = {'rungs': [], 'device_comments': normalize_device_comments(
            {row['address']: row['comment'] for row in compact['device_comments']})}
    return {'mode': 'partial', **ladder, 'delete_rung_ids': compact['delete_rung_ids']}


def compact_repair_rungs(rungs):
    """Return lossless short repair context, or None for unsupported metadata.

    This is presentation only. The candidate and repair scope retain their
    canonical representation, including layout and branch identities.
    """
    def simple(element):
        if element["type"] == "COMPARE":
            text = element["expression"]
        else:
            text = f"{element['type']} {element['address']}"
        if _simple_input(text, "r") != element:
            raise ValueError("input cannot be represented losslessly")
        return text

    def input_value(element):
        if element["type"] != "parallel_block":
            return simple(element)
        value = {"or": [[simple(child) for child in branch] for branch in element["branches"]]}
        if _branch_input(value, "r") != element:
            raise ValueError("parallel input cannot be represented losslessly")
        return value

    def output(element):
        kind = element["type"]
        if kind == "APP_INSTR":
            # APP preserves even a native opcode that aliases a typed output.
            text = " ".join(["APP", element["opcode"], *element["operands"]])
        elif kind in {"TIMER", "COUNTER"}:
            text = f"{kind} {element['address']} {element['value']}"
        else:
            text = f"{kind} {element['address']}"
        if _output(text, "r") != element:
            raise ValueError("output cannot be represented losslessly")
        return text

    result = []
    try:
        for rung in rungs:
            if (set(rung) != {"rung_id", "header_element", "shared_inputs", "branches"}
                    or rung["header_element"] is not None):
                return None
            row = {"rung_id": rung["rung_id"], "c": [simple(e) for e in rung["shared_inputs"]], "b": []}
            for index, branch in enumerate(rung["branches"], 1):
                if (set(branch) != {"branch_id", "y_offset_level", "inputs", "outputs"}
                        or branch["branch_id"] != index or branch["y_offset_level"] != index - 1):
                    return None
                row["b"].append({"i": [input_value(e) for e in branch["inputs"]],
                                 "o": [output(e) for e in branch["outputs"]]})
            result.append(row)
    except (KeyError, TypeError, ValueError):
        return None
    return result


def canonical_compact_example(*, partial=False):
    """One valid wire example; these example devices are not project allocations."""
    example = {"r": [{"c": ["NO M0"], "b": [{"i": [], "o": ["COIL M1"]}]}],
               "device_comments": [{"address": "M0", "comment": "使能"}, {"address": "M1", "comment": "输出"}]}
    if partial:
        example['r'][0]['rung_id'] = 1
        example.update(mode='partial', delete_rung_ids=[])
    return example


def compact_protocol_prompt(*, partial=False, bindings=True):
    """Render required fields from the actual schema, not a second field list."""
    from plc.ladder_limits import GXWORKS2_MAX_LADDER_BLOCK_ROWS
    schema = compact_response_schema(partial=partial, bindings=bindings)
    rung = schema["properties"]["r"]["items"]
    branch = rung["properties"]["b"]["items"]
    return (
        "\n# Compact wire representation " + (PROTOCOL_VERSION if bindings else LEGACY_PROTOCOL_VERSION) + "\n"
        + "程序对象必填字段：" + "、".join(schema["required"]) + "；r 为梯级数组，device_comments 为软元件用途注释数组，无可用注释时写 []。\n"
        + "每个梯级必填字段：" + "、".join(rung["required"]) + "。\n"
        + "每个输出分支必填字段：" + "、".join(branch["required"]) + "。\n"
        + "c 是所有输出分支共用的串联条件列表，i 是各分支追加的串联条件列表；二者始终为数组，无条件时写 []，不省略。\n"
        + "r 是梯级数组，b 是输出分支数组，i 本身是一维串联列表，o 是非空输出数组。\n"
        + f"GX Works2 每个连接的并联网络最多 {GXWORKS2_MAX_LADDER_BLOCK_ROWS} 行；共用 c/h 时，各 b 的输入并联高度与多输出高度合计。增加同一网络的 b 分支不能降低总高度。生成前安排可导出的分组，保持条件求值次数、读写顺序和触发行为。\n"
        + "结构示例（不是本项目地址分配）：" + json.dumps(canonical_compact_example(partial=partial), separators=(",", ":")) + "\n"
        + "简单输入优先写 NO/NC/P/F 加地址；等价 Mitsubishi 触点助记符 "
        + "/".join(_MITSUBISHI_CONTACT_ALIASES)
        + " 也合法并由本地按触点语义归一化；裸位地址表示 NO。比较写前缀表达式，例如 >= D0 K1。\n"
        + 'OR 只在 i 内用 {"or":[[简单输入,...],[简单输入,...]]}；子数组是串联支路，不嵌套 OR。\n'
        + "标准输出：" + ", ".join(sorted(_TYPED_OUTPUTS)) + "；COIL/PLS/PLF 后接地址，TIMER/COUNTER 后接地址和设定值。\n"
        + "手册中的原生 OUT 在线圈、定时器和计数器位置分别用 COIL、TIMER 和 COUNTER 表示。\n"
        + "其他输出直接写 opcode 与空格分隔的 operands，例如 MOV K1 D0。\n"
        + "使用应用指令前查证当前 CPU 与梯形图的原生形式。按资料逐项填写操作数数量、顺序、数据类型和设备条件，不自行补造或省略操作数。拟用写法缺资料时先检索 RAG，仍缺的部分保留为未知并采用有依据的可行写法。\n"
        + ('对已明确电平的现场条件，使用 {"condition_ref":"资料中的 binding_id","when":"active|inactive|run_permit"}，when 只填一个已交付的条件名，由 Core 展开对应电平。\n'
           '应用指令使用 {"form_ref":"资料中的形式编号","parameters":[{"name":"资料中的参数名","value":"操作数"}]}。每个参数恰好填写一次，按名称绑定；不得将另一型号、语言或宽度的写法套用到当前指令。\n'
           '整数参数若表示已选时基下的节拍数，可提交 value={"duration_ms":时长,"tick_ms":每节拍毫秒数}。'
           '普通 TIMER 的时基已查证时，可提交 {"timer":"T 地址","duration_ms":时长,"tick_ms":每节拍毫秒数}。'
           'Core 精确换算，非整倍数由实现选择解决。\n' if bindings else '')
        + 'device_comments 每项为 {"address":"软元件地址","comment":"用途短名称"}，'
        + f"用途名称最多 {GXWORKS2_DEVICE_COMMENT_MAX_CHARS} 字符；为程序中用途明确的软元件填写，保留原文及逐项问答已明确的用途，不为注释新增追问。\n"
        + "注释仅用于展示，不作为电平、接线或控制语义的依据；已确认名称由本地优先补齐。分支编号和布局由本地补齐；仅输出协议 JSON。\n"
        + ('mode 必须为 partial；r 只列修改/新增的完整梯级，每项 rung_id 对应当前程序的原编号，新增使用未占用编号。'
           'delete_rung_ids 只列明确删除的梯级编号，无删除写 []；未列出的梯级和注释由本地保留。'
           '只改注释或只删除时 r 可为空。不得输出 branch_id、y_offset_level。\n' if partial else '')
    )


LADDER_MAINTAINABILITY_PROMPT = (
    "优先用简洁、可读、扫描开销合理的梯形图实现工艺。"
    "同一逻辑仅地址、索引或常数不同，优先评估当前 CPU 有资料支持的成组操作或复用结构。"
    "精简后须保持工艺要求的时序、并发处理能力、写入顺序以及状态与输出行为。"
)


def compact_generation_prompt(*, partial=False, bindings=True):
    from plc.generation_contract import CYCLIC_ASSIGNMENT_PROMPT
    return ("# Compact ladder generation\n"
        + ("按本轮修改更新当前程序，只返回局部梯级计划。\n" if partial else
           f"实现已确认规格，返回一个紧凑梯级计划。\n{LADDER_MAINTAINABILITY_PROMPT}\n")
        + CYCLIC_ASSIGNMENT_PROMPT + """
- 实现已确认 I/O、参数、电平及 selected_approach.generation_contract；unverified_constraints 不升级为硬约束。
- selected_approach.generation_contract 或 implementation_preferences 的 instruction_instances 中，opcode 与 operands 必须逐项原样使用，不省略、替换或重排。
- NO 检查位=1，NC 检查位=0；active_when 与 run_permit_when 使用下方绑定谓词。
- 不新增未确认 X/Y、停止/急停、硬件或模块寄存器。内部状态用普通 M/D/T/C；特殊软元件遵守本型号证据及显式禁用。
- COIL 每扫描写导通真假；同一普通 Y/M 一个 COIL，完整条件放在同一输入路径，输出 branch 不是输入 OR。
- 比较输入只比较；算术先用应用指令写入寄存器。
- TIMER 使用 T，COUNTER 使用 C；普通定时器须有可变为 FALSE 的使能/复位路径。
- 一次事件使用 P/F 边沿，持续条件使用电平；两者不互换。
""" + compact_protocol_prompt(partial=partial, bindings=bindings))


def compact_capability_prompt(plc_model, confirmed_spec):
    """Compatibility entry.

    Instruction contracts are delivered once through structured PLC facts.
    The compact wire protocol owns representation only and must not duplicate
    registry/manual engineering facts into Agent B's prompt.
    """
    return ""
