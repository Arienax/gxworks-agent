"""Deterministic extraction of low-level constraints explicitly fixed by the user.

Agent A does not own opcode choice, internal-device allocation, or instruction
operand layout. This module extracts only low-level choices the caller already
made in their own text, then merges those choices across pinned reanalysis.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping

from plc.device_identity import DEVICE_TOKEN_RE, canonical_device
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY


EXPLICIT_USER_CONSTRAINTS_VERSION = 1
_FIELDS = (
    "required_opcodes",
    "forbidden_opcodes",
    "required_devices",
    "forbidden_devices",
    "instruction_instances",
)
_NEGATIVE = re.compile(
    r"(?:不要|不使用|不采用|禁用|禁止|不得|避免|不用|do\s+not\s+use|must\s+not\s+use|avoid)",
    re.IGNORECASE,
)
_POSITIVE = re.compile(
    r"(?:必须|务必|明确(?:要求)?|固定|指定|采用|使用|改用|用|must\s+use|required?|use)",
    re.IGNORECASE,
)
_COMPARISON = re.compile(
    r"(?:比较|对比|区别|哪(?:个|种)|还是|或者|或|vs\.?|versus|compare|choose\s+between)",
    re.IGNORECASE,
)
_CLEAR = re.compile(
    r"(?:清除|取消|解除|不再固定|重新开放|clear|remove|unfix).{0,20}"
    r"(?:固定)?(?:指令实例|指令|opcode|设备|软元件|device|约束|constraint)",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"(?<![A-Za-z0-9_.$@+<>!=\-])([A-Za-z][A-Za-z0-9_.$@+<>!=\-]{0,63})(?![A-Za-z0-9_.$@+<>!=\-])")
_OPERAND = re.compile(
    r"^(?:K[+-]?\d+|H[0-9A-F]+|(?:SM|SD|TS|TC|CS|CC|ER|X|Y|M|S|T|C|D|R|V|Z|P|I)\d+(?:[VZ]\d+)?|[+-]?\d+(?:\.\d+)?)$",
    re.IGNORECASE,
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
        result["instruction_instances"].append(
            {"opcode": opcode, "operands": normalized_operands}
        )
    return result


def _directive_context(text, start, *, width=96):
    prefix = text[max(0, start - width):start]
    sentence = re.split(r"[。；;\n]", prefix)[-1]
    return prefix, sentence


def _directive_kind(text, start):
    prefix, sentence = _directive_context(text, start)
    if _NEGATIVE.search(sentence):
        return "forbidden"
    if _POSITIVE.search(sentence):
        return "required"
    if _COMPARISON.search(sentence):
        return "comparison"
    return ""


def _operand_tokens(text):
    return [
        token
        for token in re.split(r"[\s,，;；。]+", text.strip())
        if token
    ]


def _line_like_instance(text, start, end):
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    before = text[line_start:start].strip(" \t:-：")
    after = text[end:line_end].strip(" \t,，;；。")
    return not before and not after


def extract_explicit_user_constraints(text, plc_model="FX3U"):
    """Return current-turn explicit constraints plus non-persistent clear commands."""
    source = str(text or "")
    constraints = normalize_explicit_user_constraints({})
    instance_spans = []

    for match in _TOKEN.finditer(source):
        opcode = match.group(1).upper()
        resolved = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu=plc_model)
        if resolved is None or not resolved.spec.supports_cpu(str(plc_model or "").upper()):
            continue
        spec = resolved.spec
        directive = _directive_kind(source, match.start())
        tail = source[match.end():]
        tokens = _operand_tokens(tail)
        operand_count = (
            spec.min_operands
            if spec.min_operands is not None
            and spec.min_operands == spec.max_operands
            else len(spec.operands) if spec.operands else None
        )
        instance = None
        if operand_count is not None and operand_count > 0 and len(tokens) >= operand_count:
            operands = [token.strip("()（）[]{}:：;；。") for token in tokens[:operand_count]]
            if all(_OPERAND.fullmatch(token) for token in operands):
                raw_end = match.end()
                cursor = source[match.end():]
                consumed = 0
                found = 0
                for token_match in re.finditer(r"[^\s,，;；。]+", cursor):
                    found += 1
                    consumed = token_match.end()
                    if found == operand_count:
                        break
                raw_end += consumed
                # Operands belong to this instruction mention, even when the
                # mention is only comparative. Do not duplicate them as
                # standalone required_devices.
                instance_spans.append((match.start(), raw_end))
                prefix, sentence = _directive_context(source, match.start())
                explicitly_fixed = directive == "required" or (
                    not _COMPARISON.search(sentence)
                    and _line_like_instance(source, match.start(), raw_end)
                    and bool(_POSITIVE.search(prefix))
                )
                if explicitly_fixed:
                    instance = {"opcode": opcode, "operands": operands}
                    constraints["instruction_instances"].append(instance)
                    if opcode not in constraints["required_opcodes"]:
                        constraints["required_opcodes"].append(opcode)
        if instance is not None:
            continue
        if directive == "forbidden":
            if opcode not in constraints["forbidden_opcodes"]:
                constraints["forbidden_opcodes"].append(opcode)
        elif directive == "required":
            if opcode not in constraints["required_opcodes"]:
                constraints["required_opcodes"].append(opcode)

    def inside_instance(start, end):
        return any(a <= start and end <= b for a, b in instance_spans)

    for match in DEVICE_TOKEN_RE.finditer(source):
        if inside_instance(match.start(), match.end()):
            continue
        directive = _directive_kind(source, match.start())
        device = canonical_device(match.group(0).upper())
        if directive == "forbidden":
            if device not in constraints["forbidden_devices"]:
                constraints["forbidden_devices"].append(device)
        elif directive == "required":
            if device not in constraints["required_devices"]:
                constraints["required_devices"].append(device)

    clear_fields = []
    for match in _CLEAR.finditer(source):
        token = match.group(0).casefold()
        if any(word in token for word in ("指令实例", "指令", "opcode")):
            clear_fields.extend(("instruction_instances", "required_opcodes", "forbidden_opcodes"))
        elif any(word in token for word in ("设备", "软元件", "device")):
            clear_fields.extend(("required_devices", "forbidden_devices"))
        else:
            clear_fields.extend(_FIELDS)

    return {
        "constraints": normalize_explicit_user_constraints(constraints),
        "clear_fields": list(dict.fromkeys(clear_fields)),
    }


def merge_explicit_user_constraints(previous, update, *, clear_fields=()):
    result = normalize_explicit_user_constraints(previous)
    current = normalize_explicit_user_constraints(update)
    for field in clear_fields:
        if field in result and isinstance(result[field], list):
            result[field] = []

    for required, forbidden in (
        ("required_opcodes", "forbidden_opcodes"),
        ("required_devices", "forbidden_devices"),
    ):
        for item in current[required]:
            result[forbidden] = [value for value in result[forbidden] if value != item]
            if item not in result[required]:
                result[required].append(item)
        for item in current[forbidden]:
            result[required] = [value for value in result[required] if value != item]
            if item not in result[forbidden]:
                result[forbidden].append(item)

    if current["instruction_instances"]:
        replacement_opcodes = {item["opcode"] for item in current["instruction_instances"]}
        result["instruction_instances"] = [
            item for item in result["instruction_instances"]
            if item["opcode"] not in replacement_opcodes
        ]
        result["instruction_instances"].extend(copy.deepcopy(current["instruction_instances"]))

    return normalize_explicit_user_constraints(result)


__all__ = [
    "EXPLICIT_USER_CONSTRAINTS_VERSION",
    "extract_explicit_user_constraints",
    "merge_explicit_user_constraints",
    "normalize_explicit_user_constraints",
]
