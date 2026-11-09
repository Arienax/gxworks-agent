"""Explicit migration helpers for pre-structured approach metadata.

Fresh analysis and normalization must never derive hard constraints from prose.
Only callers handling a persisted legacy shape should enter this module.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping


_NEGATION_RE = re.compile(r"(?:不(?:使用|采用|用|设)|禁止|不得|无需|不要)")

# Frozen vocabulary used only to recover advisory metadata from historical
# generation_guide text. It is not a fresh-analysis instruction whitelist.
_KNOWN_OPCODE_HINTS = {
    "MOV", "DMOV", "INC", "DEC", "CMP", "DCMP", "SET", "RST", "ALT", "ALTP",
    "SFTL", "SFTLP", "PLS", "PLF", "PLSY", "PLSV", "DRVI", "DRVA", "ZRN",
    "DSZR", "DVIT", "TO", "FROM", "RS", "RS2", "ADPRW", "PID", "DECO", "ENCO",
    "BCD", "BIN", "ADD", "SUB", "MUL", "DIV", "WAND", "WOR", "WXOR", "ROL",
    "ROR", "STL", "RET",
}


def _opcode_mentions(text):
    mentions = []
    value = str(text or "")
    for opcode in sorted(_KNOWN_OPCODE_HINTS, key=lambda item: (-len(item), item)):
        for match in re.finditer(
            rf"(?<![A-Za-z0-9_]){re.escape(opcode)}(?![A-Za-z0-9_])",
            value,
            re.IGNORECASE,
        ):
            mentions.append((match.start(), match.end(), opcode))
    return sorted(mentions)


def _infer_contract_from_guide(approach):
    """Recover historical prose constraints as *unverified* migration input."""

    guide = str((approach or {}).get("generation_guide") or "").strip()
    name = str((approach or {}).get("name") or "").strip()
    description = str((approach or {}).get("description") or "").strip()
    text = "\n".join(item for item in (name, description, guide) if item)
    lower = text.casefold()

    required_opcodes = []
    forbidden_opcodes = []
    any_opcode_groups = []
    mentions = _opcode_mentions(guide)
    for start, _end, opcode in mentions:
        prefix = guide[max(0, start - 12) : start]
        target = forbidden_opcodes if _NEGATION_RE.search(prefix) else required_opcodes
        if opcode not in target:
            target.append(opcode)

    for sentence in re.split(r"[；;。\n]", guide):
        sentence_mentions = list(
            dict.fromkeys(item[2] for item in _opcode_mentions(sentence))
        )
        if len(sentence_mentions) >= 2 and re.search(r"或|任选|二选一|之一", sentence):
            any_opcode_groups.append(sentence_mentions)
            required_opcodes = [
                item for item in required_opcodes if item not in sentence_mentions
            ]

    required_structures = []
    forbidden_structures = []
    any_structure_groups = []

    state_forbidden = bool(
        re.search(r"(?:不设|不用|不采用|禁止|不得使用).{0,6}状态机", lower)
    )
    register_state = bool(
        re.search(r"(?:寄存器|D\d+).{0,20}(?:状态机|步进|状态)", text, re.I)
        or (
            "状态机" in lower
            and "MOV" in {item[2] for item in mentions}
            and bool(re.search(r"D\d+", text, re.I))
        )
        or "block_input" in lower
    )
    bit_state = bool(
        re.search(r"(?:M|S)状态位|位状态机|状态继电器", text, re.I)
        or (
            "状态机" in lower
            and bool(re.search(r"M\d+", text, re.I))
            and ({"SET", "RST"} & {item[2] for item in mentions})
        )
    )
    generic_state = "状态机" in lower or "步进状态" in lower
    if state_forbidden:
        forbidden_structures.extend(["register_state_machine", "bit_state_machine"])
        required_structures.append("direct_logic")
    elif register_state:
        required_structures.extend(
            ["register_state_machine", "state_initialization", "state_comparison", "state_transition"]
        )
    elif bit_state:
        required_structures.extend(
            ["bit_state_machine", "state_initialization", "state_transition"]
        )
    elif generic_state:
        any_structure_groups.append(["register_state_machine", "bit_state_machine"])

    if any(term in lower for term in ("直接逻辑", "独立梯级")) and not generic_state:
        required_structures.append("direct_logic")
    if any(term in lower for term in ("自保持", "自锁")):
        required_structures.append("self_hold")
    if any(term in lower for term in ("硬件计数器", "内置计数器")) or re.search(
        r"OUT\s+C\d+", text, re.I
    ):
        required_structures.append("hardware_counter")
    if "INC" in {item[2] for item in mentions} and re.search(r"D\d+", guide, re.I):
        required_structures.append("data_register_counter")
    if any(term in lower for term in ("上升沿", "下降沿", "边沿")):
        required_structures.append("edge_trigger")
    if {"PLSY", "PLSV", "DRVI", "DRVA", "ZRN", "DSZR", "DVIT"} & {
        item[2] for item in mentions
    }:
        required_structures.append("pulse_positioning")
    if any(term in lower for term in ("模拟量", "0-10v", "4-20ma")):
        required_structures.append("analog_control")
    if any(term in lower for term in ("rs485", "modbus", "串行通讯", "串行通信")):
        required_structures.append("serial_communication")
    if "pid" in lower:
        required_structures.append("pid_control")
    if any(term in lower for term in ("多段速", "stf", "rh", "rm", "rl")):
        required_structures.append("vfd_multi_speed")

    return {
        "required_opcodes": required_opcodes,
        "forbidden_opcodes": forbidden_opcodes,
        "required_devices": [],
        "forbidden_devices": [],
        "required_structures": list(dict.fromkeys(required_structures)),
        "forbidden_structures": list(dict.fromkeys(forbidden_structures)),
        "any_of_opcode_groups": any_opcode_groups,
        "any_of_structure_groups": any_structure_groups,
        "source": "inferred",
    }


def _needs_legacy_approach_migration(approach):
    if not isinstance(approach, Mapping) or not approach:
        return False
    if "implementation_semantics" in approach or "explicit_user_constraints" in approach:
        return False
    contract = approach.get("generation_contract")
    if isinstance(contract, Mapping) and contract:
        return False
    return any(
        str(approach.get(key) or "").strip()
        for key in ("name", "description", "generation_guide")
    )


def is_legacy_confirmed_spec(spec):
    """Recognize persisted pre-provenance specs; fresh drafts must bypass migration."""

    if not isinstance(spec, Mapping) or not spec:
        return False
    try:
        schema_version = int(spec.get("schema_version") or 0)
    except (TypeError, ValueError):
        schema_version = 0
    if schema_version >= 4:
        return False
    # Fresh review drafts carry provenance/intent even while editable schema=3.
    if isinstance(spec.get("intent_context"), Mapping):
        return False
    if isinstance(spec.get("decision_receipt"), Mapping):
        return False
    candidates = [
        *(spec.get("approaches") or [] if isinstance(spec.get("approaches"), list) else []),
        spec.get("selected_approach"),
    ]
    return any(
        _needs_legacy_approach_migration(item)
        for item in candidates
        if isinstance(item, Mapping)
    )


def migrate_legacy_approach(approach):
    """Attach an inferred legacy contract only to an unmistakable old shape."""

    migrated = copy.deepcopy(dict(approach)) if isinstance(approach, Mapping) else {}
    if _needs_legacy_approach_migration(migrated):
        migrated["generation_contract"] = _infer_contract_from_guide(migrated)
    return migrated


def migrate_legacy_confirmed_spec(spec):
    """Migrate only a recognized persisted legacy snapshot."""

    migrated = copy.deepcopy(dict(spec)) if isinstance(spec, Mapping) else {}
    if not is_legacy_confirmed_spec(migrated):
        return migrated
    approaches = migrated.get("approaches")
    if isinstance(approaches, list):
        migrated["approaches"] = [
            migrate_legacy_approach(item) if isinstance(item, Mapping) else item
            for item in approaches
        ]
    if isinstance(migrated.get("selected_approach"), Mapping):
        migrated["selected_approach"] = migrate_legacy_approach(
            migrated["selected_approach"]
        )
    return migrated


__all__ = [
    "is_legacy_confirmed_spec",
    "migrate_legacy_approach",
    "migrate_legacy_confirmed_spec",
]
