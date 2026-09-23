"""Deterministic contracts that bind a selected implementation approach.

The requirement-analysis model may propose different implementation methods,
but the selected method is a user decision rather than advisory prose.  This
module turns that decision into a small, machine-checkable contract and checks
the generated ladder against it.  It deliberately works on ladder JSON only;
no model call is involved in compliance validation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence


CONTRACT_SCHEMA_VERSION = 1

SUPPORTED_STRUCTURES = {
    "direct_logic",
    "register_state_machine",
    "bit_state_machine",
    "state_initialization",
    "state_comparison",
    "state_transition",
    "self_hold",
    "set_reset_latch",
    "hardware_counter",
    "data_register_counter",
    "edge_trigger",
    "pulse_positioning",
    "analog_control",
    "serial_communication",
    "pid_control",
    "vfd_multi_speed",
}

STRUCTURE_LABELS = {
    "direct_logic": "直接逻辑",
    "register_state_machine": "D寄存器步进状态机",
    "bit_state_machine": "M/S位状态机",
    "state_initialization": "状态初始化",
    "state_comparison": "状态比较分区",
    "state_transition": "显式状态转移",
    "self_hold": "自保持回路",
    "set_reset_latch": "SET/RST锁存",
    "hardware_counter": "PLC硬件计数器",
    "data_register_counter": "数据寄存器加减计数",
    "edge_trigger": "边沿触发",
    "pulse_positioning": "脉冲/定位控制",
    "analog_control": "模拟量控制",
    "serial_communication": "串行通信",
    "pid_control": "PID控制",
    "vfd_multi_speed": "变频器多段速端子控制",
}

# Structure semantics may declare machine-checkable obligations without giving
# semantic_validation structure-specific code.  Selectors and coverage modes are
# generic topology primitives owned by the validator; this table only states
# what a confirmed structure requires from canonical I/O bindings.
_STRUCTURE_OBLIGATIONS = {
    "self_hold": {
        "instance_selector": "feedback_coil",
        "required_roles": ("start", "stop"),
        "distinct_roles": (("start", "stop"),),
        "predicates": (
            {
                "role": "start",
                "predicate_key": "active_when",
                "coverage": "any_non_feedback_path",
                "independent_of_feedback": True,
            },
            {
                "role": "stop",
                "predicate_key": "run_permit_when",
                "coverage": "all_paths",
            },
        ),
    },
}

_DEVICE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:SM|SD|TS|TC|CS|CC|[XYMSTCDRVZ])\d+(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_STATE_COMPARE_RE = re.compile(
    r"(?:^|\s)(?:=|==)\s*(D\d+)\s*K([+-]?\d+)(?:\s|$)",
    re.IGNORECASE,
)
_CONSTANT_RE = re.compile(r"^K([+-]?\d+)$", re.IGNORECASE)
_FIELD_ALIASES = {
    "required_instructions": "required_opcodes",
    "forbidden_instructions": "forbidden_opcodes",
    "required_features": "required_structures",
    "forbidden_features": "forbidden_structures",
    "one_of_opcodes": "any_of_opcode_groups",
    "one_of_structures": "any_of_structure_groups",
}


def _unique_strings(values, *, upper=False, lower=False):
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Sequence):
        return []
    result = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if upper:
            text = text.upper()
        if lower:
            text = text.casefold()
        if text not in result:
            result.append(text)
    return result


def _as_list(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    return []


def _normalize_groups(values, *, upper=False, lower=False):
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    result = []
    for group in values:
        normalized = _unique_strings(group, upper=upper, lower=lower)
        if normalized and normalized not in result:
            result.append(normalized)
    return result

def normalize_instruction_instances(values):
    """Normalize exact opcode+operand calls without inferring or rewriting operands."""
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    result = []
    seen = set()
    for item in values:
        if not isinstance(item, Mapping):
            continue
        opcode = str(item.get("opcode") or "").strip().upper()
        operands = item.get("operands")
        if not re.fullmatch(r"[$A-Z][A-Z0-9_.$@+<>!=\-]{0,63}", opcode):
            continue
        if not isinstance(operands, Sequence) or isinstance(operands, (str, bytes)):
            continue
        normalized_operands = []
        valid = True
        for operand in operands:
            if not isinstance(operand, str):
                valid = False
                break
            token = operand.strip()
            if not token or len(token) > 256:
                valid = False
                break
            normalized_operands.append(token)
        if not valid:
            continue
        marker = (opcode, tuple(normalized_operands))
        if marker in seen:
            continue
        seen.add(marker)
        result.append({"opcode": opcode, "operands": normalized_operands})
    return result


IMPLEMENTATION_SEMANTIC_KINDS = frozenset({"structure"})
IMPLEMENTATION_SEMANTIC_STATUSES = frozenset({"required", "forbidden", "any_of"})


def _structure_token(value):
    text = str(value or "").strip()
    if not text:
        return None
    folded = text.casefold()
    if folded in SUPPORTED_STRUCTURES:
        return folded
    labels = {label.casefold(): key for key, label in STRUCTURE_LABELS.items()}
    return labels.get(folded)


def structure_obligations(value):
    """Return declarative machine obligations for one canonical structure token."""
    token = _structure_token(value)
    return copy.deepcopy(_STRUCTURE_OBLIGATIONS.get(token) or {})



def normalize_implementation_semantics(values):
    """Normalize Agent-A architecture choices only; low-level choices belong to Core/B."""
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    result = []
    seen = set()
    for raw in values:
        if not isinstance(raw, Mapping):
            continue
        kind = str(raw.get("kind") or "").strip().casefold()
        status = str(raw.get("status") or "required").strip().casefold()
        if kind != "structure" or status not in IMPLEMENTATION_SEMANTIC_STATUSES:
            continue
        if status == "any_of":
            source = raw.get("values")
            if isinstance(source, str):
                source = [source]
            if not isinstance(source, Sequence):
                continue
            values = []
            for value in source:
                token = _structure_token(value)
                if token and token not in values:
                    values.append(token)
            if not values:
                continue
            item = {"kind": "structure", "status": status, "values": values}
            marker = ("structure", status, tuple(values))
        else:
            value = _structure_token(raw.get("value"))
            if not value:
                continue
            item = {"kind": "structure", "status": status, "value": value}
            marker = ("structure", status, value)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def project_semantics_to_generation_contract(
    values, *, explicit_user_constraints=None, source="analysis_semantics"
):
    """Project architecture semantics plus Core-owned user constraints."""
    semantics = normalize_implementation_semantics(values)
    from plc.specification.explicit_constraints import normalize_explicit_user_constraints
    explicit = normalize_explicit_user_constraints(explicit_user_constraints)
    contract = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "required_opcodes": list(explicit["required_opcodes"]),
        "forbidden_opcodes": list(explicit["forbidden_opcodes"]),
        "required_devices": list(explicit["required_devices"]),
        "forbidden_devices": list(explicit["forbidden_devices"]),
        "required_structures": [],
        "forbidden_structures": [],
        "any_of_opcode_groups": [],
        "any_of_structure_groups": [],
        "instruction_instances": copy.deepcopy(explicit["instruction_instances"]),
        "enforce": True,
        "source": source,
    }
    for item in semantics:
        status = item["status"]
        if status == "any_of":
            contract["any_of_structure_groups"].append(list(item["values"]))
            continue
        key = "required_structures" if status == "required" else "forbidden_structures"
        if item["value"] not in contract[key]:
            contract[key].append(item["value"])
    return contract


_CONTRACT_VALUE_FIELDS = (
    "required_opcodes",
    "forbidden_opcodes",
    "required_devices",
    "forbidden_devices",
    "required_structures",
    "forbidden_structures",
)
_CONTRACT_GROUP_FIELDS = (
    "any_of_opcode_groups",
    "any_of_structure_groups",
)


def _explicit_contract_fields(raw):
    """Return constraint fields explicitly supplied by the structured contract.

    ``generation_guide`` inference is a compatibility source only when the
    entire structured contract is missing/empty. A present partial contract
    never gets additional constraints from prose in omitted dimensions.
    """

    return {
        key
        for key in (*_CONTRACT_VALUE_FIELDS, *_CONTRACT_GROUP_FIELDS)
        if key in raw
    }


def _resolve_required_forbidden_conflicts(contract, explicit_fields, warnings):
    for required_key, forbidden_key, label in (
        ("required_opcodes", "forbidden_opcodes", "指令"),
        ("required_devices", "forbidden_devices", "软元件"),
        ("required_structures", "forbidden_structures", "结构"),
    ):
        required = list(contract.get(required_key) or [])
        forbidden = list(contract.get(forbidden_key) or [])
        overlap = sorted(set(required) & set(forbidden))
        if not overlap:
            continue

        required_explicit = required_key in explicit_fields
        forbidden_explicit = forbidden_key in explicit_fields
        if required_explicit and forbidden_explicit:
            # A structured contract contradicting itself is a real definition
            # error and must remain visible to contract_definition_issues().
            continue
        if required_explicit:
            contract[forbidden_key] = [item for item in forbidden if item not in overlap]
            warnings.append(
                f"显式必用{label}覆盖了 generation_guide 推断的禁用约束：{', '.join(overlap)}"
            )
        elif forbidden_explicit:
            contract[required_key] = [item for item in required if item not in overlap]
            warnings.append(
                f"显式禁用{label}覆盖了 generation_guide 推断的必用约束：{', '.join(overlap)}"
            )
        else:
            # Text inference contradicted itself.  Do not invent a winner and
            # do not block confirmation: remove the ambiguous hard constraint.
            contract[required_key] = [item for item in required if item not in overlap]
            contract[forbidden_key] = [item for item in forbidden if item not in overlap]
            warnings.append(
                f"generation_guide 对同一{label}同时推断为必用和禁用，已取消该歧义约束：{', '.join(overlap)}"
            )


def _resolve_any_of_groups(
    contract,
    *,
    group_key,
    required_key,
    forbidden_key,
    label,
    explicit_fields,
    warnings,
    definition_errors,
):
    groups = list(contract.get(group_key) or [])
    required = set(contract.get(required_key) or [])
    forbidden = set(contract.get(forbidden_key) or [])
    group_explicit = group_key in explicit_fields
    forbidden_explicit = forbidden_key in explicit_fields
    result = []

    for group in groups:
        original = list(group)
        # A separately required member already satisfies this any-of clause.
        if required.intersection(original):
            continue

        legal = [item for item in original if item not in forbidden]
        if not legal:
            if group_explicit and forbidden_explicit:
                definition_errors.append(
                    f"{label}任选组没有可用候选：{', '.join(original)} 全部被显式禁止"
                )
                # Preserve the clause for inspection/signatures.  Validation is
                # blocked by definition_errors rather than silently weakening it.
                if original not in result:
                    result.append(original)
                continue
            if group_explicit and not forbidden_explicit:
                # The explicit any-of requirement outranks inferred forbids.
                contract[forbidden_key] = [
                    item
                    for item in (contract.get(forbidden_key) or [])
                    if item not in original
                ]
                forbidden.difference_update(original)
                legal = original
                warnings.append(
                    f"显式{label}任选组覆盖了 generation_guide 推断的禁用约束：{', '.join(original)}"
                )
            else:
                warnings.append(
                    f"generation_guide 推断的{label}任选组与禁用约束冲突，已取消该歧义任选组：{', '.join(original)}"
                )
                continue

        if legal not in result:
            result.append(legal)

    contract[group_key] = result


def _resolve_contract_constraints(contract, explicit_fields):
    warnings = []
    definition_errors = []

    _resolve_required_forbidden_conflicts(contract, explicit_fields, warnings)
    _resolve_any_of_groups(
        contract,
        group_key="any_of_opcode_groups",
        required_key="required_opcodes",
        forbidden_key="forbidden_opcodes",
        label="指令",
        explicit_fields=explicit_fields,
        warnings=warnings,
        definition_errors=definition_errors,
    )
    _resolve_any_of_groups(
        contract,
        group_key="any_of_structure_groups",
        required_key="required_structures",
        forbidden_key="forbidden_structures",
        label="结构",
        explicit_fields=explicit_fields,
        warnings=warnings,
        definition_errors=definition_errors,
    )
    if warnings:
        contract["normalization_warnings"] = warnings
    if definition_errors:
        contract["definition_errors"] = definition_errors
    return contract


def _constraint_values(raw):
    """Copy only known constraint dimensions; annotations never become code."""
    raw = raw if isinstance(raw, Mapping) else {}
    values = {key: _unique_strings(raw.get(key)) for key in _CONTRACT_VALUE_FIELDS}
    values.update({key: _normalize_groups(raw.get(key)) for key in _CONTRACT_GROUP_FIELDS})
    return {key: value for key, value in values.items() if value}


def _merge_unverified(target, source):
    for key, values in _constraint_values(source).items():
        for value in values:
            if value not in target.setdefault(key, []):
                target[key].append(copy.deepcopy(value))


def normalize_generation_contract(contract=None):
    """Normalize an already-structured contract without reading approach prose."""
    raw = dict(contract) if isinstance(contract, Mapping) else {}
    for alias, canonical in _FIELD_ALIASES.items():
        if canonical not in raw and alias in raw:
            raw[canonical] = raw[alias]

    explicit_fields = _explicit_contract_fields(raw)

    def value_source(key):
        return raw.get(key)

    normalized = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "required_opcodes": _unique_strings(value_source("required_opcodes"), upper=True),
        "forbidden_opcodes": _unique_strings(value_source("forbidden_opcodes"), upper=True),
        "required_devices": _unique_strings(value_source("required_devices"), upper=True),
        "forbidden_devices": _unique_strings(value_source("forbidden_devices"), upper=True),
        "required_structures": _unique_strings(value_source("required_structures")),
        "forbidden_structures": _unique_strings(value_source("forbidden_structures")),
        "any_of_opcode_groups": _normalize_groups(value_source("any_of_opcode_groups"), upper=True),
        "any_of_structure_groups": _normalize_groups(value_source("any_of_structure_groups")),
        # Explicit constraints cannot be disabled by model-authored enforce=false.
        "enforce": True,
        "source": raw.get("source") if raw.get("source") in {"explicit", "inferred", "analysis_sanitized", "analysis_semantics"} else "explicit",
    }
    # Exact instruction calls are optional but lossless when explicitly present.
    # Absence means "not supplied"; an explicit [] means "clear the prior instances".
    if "instruction_instances" in raw:
        normalized["instruction_instances"] = normalize_instruction_instances(
            raw.get("instruction_instances")
        )
    unverified = _constraint_values(raw.get("unverified_constraints"))
    if normalized["source"] == "inferred":
        # The chosen prose still reaches the generator. Historical keyword
        # guesses are not explicit machine obligations, including old saved
        # source=inferred contracts. Never upgrade them merely by reading them.
        advisory = _resolve_contract_constraints(copy.deepcopy(normalized), set())
        if advisory.get("normalization_warnings"):
            normalized["normalization_warnings"] = advisory["normalization_warnings"]
        inferred_fields = (*_CONTRACT_VALUE_FIELDS, *_CONTRACT_GROUP_FIELDS)
        _merge_unverified(unverified, {key: normalized[key] for key in inferred_fields})
        for key in inferred_fields:
            normalized[key] = []
    # Match vocabulary, not sentence meaning. Unknown descriptions are kept
    # verbatim as unverified semantics, never silently deleted or guessed.
    labels = {label.casefold(): key for key, label in STRUCTURE_LABELS.items()}

    def structure(value):
        token = value.casefold()
        return token if token in SUPPORTED_STRUCTURES else labels.get(token)

    for key in ("required_structures", "forbidden_structures"):
        known = []
        for value in normalized[key]:
            token = structure(value)
            if token is None:
                _merge_unverified(unverified, {key: [value]})
            elif token not in known:
                known.append(token)
        normalized[key] = known
    groups = []
    for group in normalized["any_of_structure_groups"]:
        tokens = [structure(value) for value in group]
        if any(token is None for token in tokens):
            # Dropping just the opaque alternatives would turn A OR unknown
            # into MUST A. Keep the whole disjunction outside the checker.
            _merge_unverified(unverified, {"any_of_structure_groups": [group]})
        else:
            tokens = list(dict.fromkeys(tokens))
            if tokens not in groups:
                groups.append(tokens)
    normalized["any_of_structure_groups"] = groups
    normalized = _resolve_contract_constraints(
        normalized, set() if normalized["source"] == "inferred" else explicit_fields)
    if unverified:
        normalized["unverified_constraints"] = unverified
    # Preserve earlier normalization notes across save/read cycles, but never
    # trust stale definition_errors; real contradictions were recomputed above.
    notes = _unique_strings([*_unique_strings(raw.get("normalization_warnings")),
                             *_unique_strings(normalized.get("normalization_warnings"))])
    if notes:
        normalized["normalization_warnings"] = notes
    return normalized


def normalize_approach(approach):
    if not isinstance(approach, Mapping) or not approach:
        return {}
    if not any(
        str(approach.get(key) or "").strip()
        for key in ("name", "description", "generation_guide", "approach_id", "id")
    ) and not isinstance(approach.get("generation_contract"), Mapping):
        return {}
    normalized = copy.deepcopy(dict(approach))
    name = str(normalized.get("name") or "").strip()
    guide = str(normalized.get("generation_guide") or "").strip()
    normalized["name"] = name
    normalized["description"] = str(normalized.get("description") or "").strip()
    normalized["pros"] = str(normalized.get("pros") or "").strip()
    normalized["cons"] = str(normalized.get("cons") or "").strip()
    normalized["generation_guide"] = guide
    approach_id = str(
        normalized.get("approach_id") or normalized.get("id") or ""
    ).strip()
    if not approach_id:
        digest = hashlib.sha256(
            json.dumps(
                {"name": name, "generation_guide": guide},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:12]
        approach_id = f"approach_{digest}"
    normalized["approach_id"] = approach_id
    normalized.pop("id", None)
    if "implementation_semantics" in normalized or "explicit_user_constraints" in normalized:
        normalized["implementation_semantics"] = normalize_implementation_semantics(
            normalized.get("implementation_semantics")
        )
        from plc.specification.explicit_constraints import normalize_explicit_user_constraints
        normalized["explicit_user_constraints"] = normalize_explicit_user_constraints(
            normalized.get("explicit_user_constraints")
        )
        normalized["generation_contract"] = project_semantics_to_generation_contract(
            normalized["implementation_semantics"],
            explicit_user_constraints=normalized["explicit_user_constraints"],
            source="analysis_semantics",
        )
    else:
        normalized["generation_contract"] = normalize_generation_contract(
            normalized.get("generation_contract")
        )
    return normalized


def contract_definition_issues(approach):
    normalized = normalize_approach(approach)
    if not normalized:
        return ["未选择实现方案"]
    contract = normalized["generation_contract"]
    issues = list(contract.get("definition_errors") or [])
    for required_key, forbidden_key, label in (
        ("required_opcodes", "forbidden_opcodes", "指令"),
        ("required_devices", "forbidden_devices", "软元件"),
        ("required_structures", "forbidden_structures", "结构"),
    ):
        conflict = sorted(
            set(contract.get(required_key) or [])
            & set(contract.get(forbidden_key) or [])
        )
        if conflict:
            issues.append(f"同一{label}同时被要求和禁止：{', '.join(conflict)}")
    # Empty contracts are valid boundaries. A chosen engineering plan may leave
    # every opcode/device choice open; prose and preferences are not obligations.
    return list(dict.fromkeys(issues))


def contract_definition_warnings(approach):
    """Checker coverage gaps are not errors in the user's control requirement."""
    contract = normalize_approach(approach).get("generation_contract") or {}
    unverified = contract.get("unverified_constraints")
    if not unverified:
        return []
    return ["部分方案语义无法自动校验，已原样保留供生成使用：" +
            json.dumps(unverified, ensure_ascii=False, separators=(",", ":"))]


def generation_contract_signature(approach):
    normalized = normalize_approach(approach)
    contract = normalized.get("generation_contract") or {}
    comparable = {
        key: contract.get(key) or []
        for key in (
            "required_opcodes",
            "forbidden_opcodes",
            "required_devices",
            "forbidden_devices",
            "required_structures",
            "forbidden_structures",
            "any_of_opcode_groups",
            "any_of_structure_groups",
            "instruction_instances",
        )
    }
    return hashlib.sha256(
        json.dumps(
            comparable,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _iter_nested_elements(elements):
    for element in elements or []:
        if not isinstance(element, Mapping):
            continue
        yield element
        if str(element.get("type") or "").casefold() == "parallel_block":
            for branch in element.get("branches") or []:
                yield from _iter_nested_elements(branch)


def _devices_in_value(value):
    return {item.upper() for item in _DEVICE_RE.findall(str(value or ""))}


def inspect_ladder_features(ladder):
    opcodes = set()
    devices = set()
    structures = set()
    state_compares = defaultdict(set)
    state_writes = defaultdict(set)
    set_devices = set()
    reset_devices = set()
    read_devices = set()
    first_scan_writes = set()
    comments = {
        str(address).upper(): str(label)
        for address, label in ((ladder or {}).get("device_comments") or {}).items()
    }

    for rung in (ladder or {}).get("rungs") or []:
        if not isinstance(rung, Mapping):
            continue
        rung_reads = set()
        rung_coils = set()
        rung_writes = set()
        rung_has_edge = False
        header = rung.get("header_element")
        input_elements = []
        if isinstance(header, Mapping):
            input_elements.append(header)
        for branch in rung.get("branches") or []:
            if not isinstance(branch, Mapping):
                continue
            input_elements.extend(_iter_nested_elements(branch.get("inputs") or []))
            for output in branch.get("outputs") or []:
                if not isinstance(output, Mapping):
                    continue
                output_type = str(output.get("type") or "").upper()
                address = str(output.get("address") or "").strip().upper()
                operands = [str(item).strip().upper() for item in output.get("operands") or []]
                if address:
                    devices.add(address)
                    rung_writes.add(address)
                for operand in operands:
                    devices.update(_devices_in_value(operand))
                if output_type == "APP_INSTR":
                    opcode = str(output.get("opcode") or "").strip().upper()
                    if opcode:
                        opcodes.add(opcode)
                    target = operands[-1] if operands else ""
                    if target:
                        rung_writes.update(_devices_in_value(target))
                    if opcode in {"MOV", "DMOV"} and len(operands) >= 2:
                        constant = _CONSTANT_RE.fullmatch(operands[0])
                        target_register = operands[1]
                        if constant and re.fullmatch(r"D\d+", target_register):
                            state_writes[target_register].add(int(constant.group(1)))
                    if opcode == "SET" and operands:
                        set_devices.update(_devices_in_value(operands[0]))
                    if opcode == "RST" and operands:
                        reset_devices.update(_devices_in_value(operands[0]))
                elif output_type == "COUNTER":
                    opcodes.update({"OUT", "COUNTER"})
                    structures.add("hardware_counter")
                elif output_type == "TIMER":
                    opcodes.update({"OUT", "TIMER"})
                elif output_type == "COIL":
                    opcodes.update({"OUT", "COIL"})
                    if address:
                        rung_coils.add(address)
                elif output_type:
                    opcodes.add(output_type)

        for element in input_elements:
            element_type = str(element.get("type") or "").upper()
            address = str(element.get("address") or "").strip().upper()
            expression = str(element.get("expression") or "").strip()
            if address:
                devices.add(address)
                read_devices.add(address)
                rung_reads.add(address)
            devices.update(_devices_in_value(expression))
            read_devices.update(_devices_in_value(expression))
            if element_type in {"P", "RISING", "F", "FALLING"}:
                rung_has_edge = True
            if element_type in {"COMPARE", "BLOCK_INPUT", "BLOCK_OUTPUT"}:
                match = _STATE_COMPARE_RE.search(expression)
                if match:
                    state_compares[match.group(1).upper()].add(int(match.group(2)))

        if rung_has_edge:
            structures.add("edge_trigger")
        if rung_coils & rung_reads:
            structures.add("self_hold")
        if rung_reads & {"M8002", "SM402", "SM8002"}:
            first_scan_writes.update(rung_writes)

    register_state_registers = {
        register
        for register, values in state_compares.items()
        if register in state_writes
        and len(values | state_writes[register]) >= 2
    }
    if register_state_registers:
        structures.update(
            {
                "register_state_machine",
                "state_comparison",
                "state_transition",
            }
        )
        if first_scan_writes & register_state_registers:
            structures.add("state_initialization")

    candidate_state_bits = {
        address
        for address in set_devices | reset_devices
        if re.fullmatch(r"(?:M|S)\d+", address)
        and address in read_devices
    }
    if register_state_registers:
        # A register state machine commonly uses separate SET/RST latches for
        # modes, alarms and run requests.  Those latches are not a second bit
        # state machine merely because two of them are read later.  When a D
        # state machine is already proven, require explicit state semantics
        # for M bits; S devices remain inherently state-relay candidates.
        state_label_re = re.compile(
            r"状态|步骤|阶段|工步|待机|就绪态|运行态|完成态|state|step",
            re.IGNORECASE,
        )
        state_bits = {
            address
            for address in candidate_state_bits
            if address.startswith("S")
            or state_label_re.search(comments.get(address, ""))
        }
    else:
        state_bits = candidate_state_bits
    if len(state_bits) >= 2 and set_devices & state_bits and reset_devices & state_bits:
        structures.update({"bit_state_machine", "state_transition"})
        if first_scan_writes & state_bits:
            structures.add("state_initialization")

    if set_devices & reset_devices:
        structures.add("set_reset_latch")
    if "INC" in opcodes or "DEC" in opcodes:
        if any(re.fullmatch(r"D\d+", item) for item in devices):
            structures.add("data_register_counter")
    if opcodes & {"PLSY", "PLSV", "DRVI", "DRVA", "ZRN", "DSZR", "DVIT"}:
        structures.add("pulse_positioning")
    uses_fx3u_analog_adapter_devices = any(
        re.fullmatch(r"[DM]82[6-9]\d", device)
        for device in devices
    )
    if opcodes & {
        "TO", "DTO", "FROM", "DFROM", "RD3A", "RD3AP", "WR3A", "WR3AP"
    } or uses_fx3u_analog_adapter_devices or any(
        re.search(r"模拟量|0-10V|4-20mA", label, re.I) for label in comments.values()
    ):
        structures.add("analog_control")
    if opcodes & {"RS", "RS2", "ADPRW"}:
        structures.add("serial_communication")
    if "PID" in opcodes:
        structures.add("pid_control")
    if any(
        re.search(r"(?:STF|RH|RM|RL|多段速)", label, re.I)
        for label in comments.values()
    ):
        structures.add("vfd_multi_speed")
    if not ({"register_state_machine", "bit_state_machine"} & structures):
        structures.add("direct_logic")

    return {
        "opcodes": sorted(opcodes),
        "devices": sorted(devices),
        "structures": sorted(structures),
        "state_registers": sorted(register_state_registers),
        "state_bits": sorted(state_bits),
    }


def validate_ladder_against_selected_approach(ladder, confirmed_spec):
    if not isinstance(confirmed_spec, Mapping):
        return []
    selected = confirmed_spec.get("selected_approach")
    if not isinstance(selected, Mapping) or not selected:
        return []
    approach = normalize_approach(selected)
    contract = approach.get("generation_contract") or {}
    if not contract.get("enforce", True):
        return []
    definition_issues = contract_definition_issues(approach)
    if definition_issues:
        return ["方案契约无效：" + "；".join(definition_issues)]

    features = inspect_ladder_features(ladder)
    opcodes = set(features["opcodes"])
    devices = set(features["devices"])
    structures = set(features["structures"])
    issues = []

    missing_opcodes = sorted(set(contract["required_opcodes"]) - opcodes)
    present_forbidden_opcodes = sorted(set(contract["forbidden_opcodes"]) & opcodes)
    missing_devices = sorted(set(contract["required_devices"]) - devices)
    present_forbidden_devices = sorted(set(contract["forbidden_devices"]) & devices)
    missing_structures = sorted(set(contract["required_structures"]) - structures)
    present_forbidden_structures = sorted(
        set(contract["forbidden_structures"]) & structures
    )
    if missing_opcodes:
        issues.append("缺少方案必用指令 " + ", ".join(missing_opcodes))
    if present_forbidden_opcodes:
        issues.append("使用了方案禁用指令 " + ", ".join(present_forbidden_opcodes))
    if missing_devices:
        issues.append("缺少方案指定软元件 " + ", ".join(missing_devices))
    if present_forbidden_devices:
        issues.append("使用了方案禁用软元件 " + ", ".join(present_forbidden_devices))
    if missing_structures:
        issues.append(
            "缺少方案结构 "
            + ", ".join(STRUCTURE_LABELS.get(item, item) for item in missing_structures)
        )
    if present_forbidden_structures:
        issues.append(
            "出现方案禁止结构 "
            + ", ".join(
                STRUCTURE_LABELS.get(item, item)
                for item in present_forbidden_structures
            )
        )
    for group in contract.get("any_of_opcode_groups") or []:
        if not (set(group) & opcodes):
            issues.append("至少需要一种指令：" + " / ".join(group))
    for group in contract.get("any_of_structure_groups") or []:
        if not (set(group) & structures):
            issues.append(
                "至少需要一种结构："
                + " / ".join(STRUCTURE_LABELS.get(item, item) for item in group)
            )
    return issues


def format_contract_summary(approach, *, localized=False):
    """Keep canonical values intact; opt in to translated presentation labels."""
    from shared.i18n import tr

    label = tr if localized else str
    normalized = normalize_approach(approach)
    contract = normalized.get("generation_contract") or {}
    parts = []
    if contract.get("required_opcodes"):
        required_opcodes = [
            (
                label("OUT（用 COIL/TIMER/COUNTER 表示）")
                if opcode == "OUT"
                else opcode
            )
            for opcode in contract["required_opcodes"]
        ]
        parts.append(label("必用指令 ") + label("/").join(required_opcodes))
    if contract.get("forbidden_opcodes"):
        parts.append(label("禁用指令 ") + label("/").join(contract["forbidden_opcodes"]))
    if contract.get("required_structures"):
        parts.append(
            label("必用结构 ")
            + label("/").join(
                label(STRUCTURE_LABELS[item]) if item in STRUCTURE_LABELS else item
                for item in contract["required_structures"]
            )
        )
    if contract.get("forbidden_structures"):
        parts.append(
            label("禁用结构 ")
            + label("/").join(
                label(STRUCTURE_LABELS[item]) if item in STRUCTURE_LABELS else item
                for item in contract["forbidden_structures"]
            )
        )
    if contract.get("required_devices"):
        parts.append(label("指定软元件 ") + label("/").join(contract["required_devices"]))
    if contract.get("instruction_instances"):
        instances = [
            " ".join([item["opcode"], *item["operands"]]).strip()
            for item in contract["instruction_instances"]
            if isinstance(item, Mapping)
        ]
        if instances:
            parts.append(label("固定指令实例 ") + label(" / ").join(instances))
    preferences = normalized.get("implementation_preferences")
    preferences = preferences if isinstance(preferences, Mapping) else {}
    if preferences.get("instruction_instances"):
        instances = [
            " ".join([item["opcode"], *item["operands"]]).strip()
            for item in normalize_instruction_instances(preferences.get("instruction_instances"))
        ]
        if instances:
            parts.append(label("方案指令实例 ") + label(" / ").join(instances))
    if contract.get("unverified_constraints"):
        parts.append(label("另有保留的未机检方案语义"))
    return label("；").join(parts)


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "SUPPORTED_STRUCTURES",
    "contract_definition_issues",
    "contract_definition_warnings",
    "format_contract_summary",
    "generation_contract_signature",
    "inspect_ladder_features",
    "normalize_approach",
    "normalize_generation_contract",
    "normalize_implementation_semantics",
    "normalize_instruction_instances",
    "project_semantics_to_generation_contract",
    "structure_obligations",
    "validate_ladder_against_selected_approach",
]
