"""Model-free ladder_v1 generation contract and safe specification projection.

This describes the generation subset of the historical ladder interchange
format. It does not lower instructions, infer PLC facts or build canonical IR.
Candidate acceptance belongs to the shared API generation pipeline.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from plc.instructions import GENERATION_TYPED_OUTPUT_OPCODES, generation_app_instr_mnemonics


MAX_LABEL_LEN = 64
APP_INSTR_OPCODE_PATTERN = r"^[A-Z0-9_.$@+\-]+$"


def _object(properties, required):
    return {
        "type": "object", "properties": properties,
        "required": list(required), "additionalProperties": False,
    }


def _array(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum}


def ladder_v1_schema(plc_model=None) -> dict:
    """Return a fresh, self-contained schema, also safe to embed in a tool."""

    label = {"type": ["string", "null"], "maxLength": MAX_LABEL_LEN}
    address = {"type": "string", "minLength": 1}
    token = {"type": "string", "minLength": 1, "pattern": r"^\S+$"}
    contact = _object({
        "type": {"enum": ["NO", "NC", "P", "F", "RISING", "FALLING"]},
        "address": address, "label": label,
    }, ["type", "address"])
    operand = r"(?:K[+-]?\d+|H[0-9A-F]+|(?:SM|SD|X|Y|M|D|T|C|S|V|Z)\d+(?:[VZ]\d+)?)"
    compare = _object({
        "type": {"enum": ["COMPARE", "BLOCK_INPUT"]},
        "expression": {
            "type": "string",
            "pattern": rf"^(?:=|==|<>|>=|<=|>|<)\s+{operand}\s+{operand}$",
            "description": "Prefix comparison, e.g. > D0 K100. Calculate arithmetic in APP_INSTR first.",
        },
        "label": label,
    }, ["type", "expression"])
    simple_input = {"oneOf": [contact, compare]}
    parallel = _object({
        "type": {"enum": ["parallel_block"]},
        "branches": _array(_array(simple_input, 1), 1),
        "label": label,
    }, ["type", "branches"])
    coil = _object({
        "type": {"enum": ["COIL", "PLS", "PLF"]},
        "address": address, "label": label,
    }, ["type", "address"])
    timer_counter = _object({
        "type": {"enum": ["TIMER", "COUNTER"]},
        "address": address, "value": token, "label": label,
    }, ["type", "address", "value"])
    opcode_rule = {
        "type": "string", "minLength": 1, "maxLength": 64,
        "pattern": APP_INSTR_OPCODE_PATTERN,
        "description": "One catalogued application opcode supported by the selected PLC; no prose or operands here.",
    }
    if plc_model:
        opcode_rule["enum"] = list(generation_app_instr_mnemonics(plc_model))
    else:
        opcode_rule["not"] = {"enum": sorted(GENERATION_TYPED_OUTPUT_OPCODES)}
    application = _object({
        "type": {"enum": ["APP_INSTR"]},
        "opcode": opcode_rule,
        "operands": _array(token), "label": label,
    }, ["type", "opcode", "operands"])
    branch = _object({
        "branch_id": {"type": "integer", "minimum": 1},
        "y_offset_level": {"type": "integer", "minimum": 0},
        "inputs": _array({"oneOf": [contact, compare, parallel]}),
        "outputs": _array({"oneOf": [coil, timer_counter, application]}, 1),
    }, ["branch_id", "y_offset_level", "inputs", "outputs"])
    rung = _object({
        "rung_id": {"type": "integer", "minimum": 0},
        "debug_note": label,
        "header_element": {"oneOf": [{"type": "null"}, contact, compare]},
        "shared_inputs": _array(simple_input),
        "branches": _array(branch, 1),
    }, ["rung_id", "header_element", "branches"])
    return copy.deepcopy(_object({
        "device_comments": {
            "type": "object",
            "additionalProperties": {"type": "string", "maxLength": MAX_LABEL_LEN},
        },
        "rungs": _array(rung, 1),
    }, ["device_comments", "rungs"]))


def generation_output_contract(*, allow_partial=False, plc_model=None) -> dict:
    """Recommended API output schema; acceptance uses the shared API parser.

    Compatible legacy encodings are normalized before structural checks.
    Engineering instructions come from the shared API context, not a second
    set of MCP-only semantic rules.
    """
    return {
        "format": "ladder_v1",
        "schema": ladder_response_schema(allow_partial=allow_partial, plc_model=plc_model),
        "validation_profile": "generation_structural",
        "acceptance": "One candidate through the shared API normalizer and structural validator. "
                      "Engineering diagnostics do not trigger automatic retries.",
        "server_owned_fields": [
            "project_id", "plc_model", "revision", "confirmed_spec", "candidate_id",
            "candidate_ir_sha256", "ladder_sha256", "networks", "instructions",
            "reads", "writes", "devices", "timing", "logic", "analysis", "io_map", "source",
        ],
    }


def validate_generation_shape(ladder: Mapping[str, Any]) -> None:
    """Check only the schema above, without an optional JSON-schema dependency.

    The legacy validator also accepts import-only BLOCK_OUTPUT expressions and
    extra rung fields. New candidates must not use those escape hatches. This
    small walker supports only the schema keywords emitted by ladder_v1_schema;
    it is not another PLC validator or a general JSON-schema implementation.
    """

    def check(value, rule, path):
        if "oneOf" in rule:
            failures = []
            for choice in rule["oneOf"]:
                try:
                    check(value, choice, path)
                    return  # Alternatives in this contract have disjoint types.
                except ValueError as error:
                    failures.append(error)
            # Select the matching element type to keep actionable field errors.
            if isinstance(value, Mapping):
                for choice, error in zip(rule["oneOf"], failures):
                    types = choice.get("properties", {}).get("type", {}).get("enum", [])
                    if value.get("type") in types:
                        raise error
            raise ValueError(f"{path}: unsupported ladder_v1 element shape")
        expected = rule.get("type")
        types = expected if isinstance(expected, list) else [expected]
        if expected and not any(
            (kind == "object" and isinstance(value, Mapping))
            or (kind == "array" and isinstance(value, list))
            or (kind == "string" and isinstance(value, str))
            or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (kind == "null" and value is None)
            for kind in types
        ):
            raise ValueError(f"{path}: expected {expected}")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"{path}: unsupported value")
        if value in rule.get("not", {}).get("enum", []):
            raise ValueError(f"{path}: use the dedicated typed output")
        if isinstance(value, Mapping):
            properties = rule.get("properties", {})
            missing = set(rule.get("required", [])) - set(value)
            if missing:
                raise ValueError(f"{path}: missing fields {sorted(missing)}")
            additional = rule.get("additionalProperties", False)
            for key, item in value.items():
                child = properties.get(key, additional)
                if child is False:
                    raise ValueError(f"{path}: unexpected field {key!r}")
                check(item, child, f"{path}.{key}")
        elif isinstance(value, list):
            if len(value) < rule.get("minItems", 0):
                raise ValueError(f"{path}: requires at least {rule['minItems']} item(s)")
            for index, item in enumerate(value):
                check(item, rule["items"], f"{path}[{index}]")
        elif isinstance(value, str):
            if len(value) < rule.get("minLength", 0) or len(value) > rule.get("maxLength", len(value)):
                raise ValueError(f"{path}: invalid text length")
            if "pattern" in rule and not re.fullmatch(rule["pattern"], value):
                raise ValueError(f"{path}: invalid token or expression format")
        elif isinstance(value, int) and "minimum" in rule and value < rule["minimum"]:
            raise ValueError(f"{path}: below minimum {rule['minimum']}")

    check(ladder, ladder_v1_schema(), "$")


# Projection templates select known engineering fields at every object level.
# None means a JSON scalar, [template] means a list. Never stringify UI objects
# or copy an arbitrary subtree from application state into an external result.
_CONTRACT_FIELDS = dict.fromkeys(("schema_version", "enforce", "source"))
_CONTRACT_FIELDS.update({key: [None] for key in (
    "required_opcodes", "forbidden_opcodes", "required_devices", "forbidden_devices",
    "required_structures", "forbidden_structures",
    "required_instructions", "forbidden_instructions", "required_features", "forbidden_features",
)})
_CONTRACT_FIELDS.update({key: [[None]] for key in (
    "any_of_opcode_groups", "any_of_structure_groups", "one_of_opcodes", "one_of_structures",
)})
# Opaque semantics remain visible to all generation adapters, but outside the
# machine-enforced required/forbidden lists. No arbitrary metadata is exposed.
_CONTRACT_FIELDS["unverified_constraints"] = {
    **{key: [None] for key in ("required_opcodes", "forbidden_opcodes",
                              "required_devices", "forbidden_devices",
                              "required_structures", "forbidden_structures")},
    **{key: [[None]] for key in ("any_of_opcode_groups", "any_of_structure_groups")},
}
_HARDWARE_FIELDS = dict.fromkeys((
    "plc_family", "cpu_full_model", "output_type", "firmware", "drive_model",
    "control_method", "control_method_label", "wiring_mapping", "motion_drive_model",
    "motion_control_method", "motion_control_method_label", "motion_wiring_mapping",
    "positioning_implementation", "positioning_module_model", "positioning_module_quantity",
    "pulse_output_axis", "direction_output", "motion_speed", "positioning_mode",
    "position_target", "homing_required", "homing_method",
))
_HARDWARE_FIELDS["modules"] = [None]
_COMPONENT_FIELDS = dict.fromkeys((
    "model", "module", "module_model", "channel", "address", "range", "signal_range",
    "digital_range", "control_method", "wiring_mapping", "output_type", "note", "notes",
    "input_module", "output_module", "input_channel", "output_channel",
    "input_range", "output_range", "scaling", "unit", "quantity",
))
_HARDWARE_CONTEXT_FIELDS = {**_HARDWARE_FIELDS, **_COMPONENT_FIELDS}
_HARDWARE_CONTEXT_FIELDS.update({key: _COMPONENT_FIELDS for key in (
    "drive", "analog_module", "analog_output", "analog_input", "motion", "positioning",
)})
EVIDENCE_RECORD_FIELDS = dict.fromkeys((
    "id", "source", "manual_id", "manual_number", "revision", "manual_type",
    "chunk_type", "instruction_opcode", "section", "page", "page_end", "pdf_page",
    "content_sha256", "role",
))
EVIDENCE_FIELDS = {
    **dict.fromkeys(("stage", "status", "query_sha256", "context_sha256", "plc_model",
                    "char_budget", "used_chars", "token_budget", "used_tokens",
                    "query_truncated", "reason")),
    "records": [EVIDENCE_RECORD_FIELDS],
    "omitted_ids": [None],
}
ENGINEERING_CONTEXT_FIELDS = {
    "schema_version": None,
    "requests": [{
        **dict.fromkeys(("id", "text", "source", "superseded_by", "text_sha256",
                        "runtime_text_status", "duplicate_of")),
        "absorbed_by_confirmed_fields": [None],
    }],
    "proposals": [{
        **dict.fromkeys(("approach_id", "proposal_sha256", "source")),
        "request_ids": [None], "evidence": EVIDENCE_FIELDS,
    }],
    "analysis_evidence": EVIDENCE_FIELDS,
    "confirmation": {
        **dict.fromkeys(("status", "source", "approach_id", "selected_sha256",
                        "fields_sha256", "selected_origin")),
        "request_ids": [None],
    },
}


_SPEC_FIELDS = {
    "engineering_context": ENGINEERING_CONTEXT_FIELDS,
    **_HARDWARE_FIELDS,  # Legacy specs sometimes store the hardware profile inline.
    **dict.fromkeys(("schema_version", "summary", "user_notes", "scan_budget_ms", "scan_warning_ms")),
    "selected_approach": {
        **dict.fromkeys(("id", "approach_id", "name", "description", "generation_guide")),
        "generation_contract": _CONTRACT_FIELDS,
        "implementation_preferences": _CONTRACT_FIELDS,
    },
    "parameters": [dict.fromkeys(("id", "name", "value", "note", "source"))],
    "io_table": [dict.fromkeys(("address", "kind", "label", "description", "source"))],
    "hardware_profile": _HARDWARE_FIELDS,
    "hardware_context": _HARDWARE_CONTEXT_FIELDS,
    "hardware_requirements": dict.fromkeys(("hardware_dependent", "vfd", "motion", "pulse", "analog", "serial")),
    "execution_semantics": [{
        **dict.fromkeys(("semantic", "type", "evidence", "source", "strict", "period_ms", "pulse_width_ms", "minimum_pulse_width_ms")),
        "devices": [None],
    }],
    "timing": dict.fromkeys(("scan_budget_ms", "scan_warning_ms")),
}
_MUTEX_FIELDS = {**dict.fromkeys(("left", "right", "source")), **{key: [None] for key in ("devices", "outputs", "pair")}}
_EXPECTATION_FIELDS = dict.fromkeys((
    "device", "address", "reader_network", "reader", "writer_network", "writer", "source",
))


def _project(value, template):
    if isinstance(template, dict) and isinstance(value, Mapping):
        return {key: _project(value[key], child) for key, child in template.items() if key in value}
    if isinstance(template, list) and isinstance(value, (list, tuple)):
        return [_project(item, template[0]) for item in value]
    if template is None and (value is None or isinstance(value, (str, int, float, bool))):
        return value
    return None


def generation_specification(confirmed_spec: Any) -> dict | None:
    """Expose generation facts only; never normalize or alter the stored spec."""

    if not isinstance(confirmed_spec, Mapping):
        return None
    result = _project(confirmed_spec, _SPEC_FIELDS)
    selected = result.get("selected_approach")
    if isinstance(selected, Mapping):
        from plc.specification.approach import normalize_generation_contract
        contract = normalize_generation_contract(selected.get("generation_contract"), approach=selected)
        if contract.get("unverified_constraints"):
            selected["generation_contract"] = _project(contract, _CONTRACT_FIELDS)
    # Review questions with no confirmed value are provenance/UI state, not
    # engineering facts. They may remain in the persisted specification, but
    # must not consume generation context or invite Agent B to invent an answer.
    if isinstance(result.get("parameters"), list):
        result["parameters"] = [
            row for row in result["parameters"]
            if isinstance(row, Mapping)
            and row.get("value") is not None
            and str(row.get("value")).strip()
        ]
    # Old specs can predate the canonical table. Do not lose their I/O constraints.
    if not result.get("io_table") and isinstance(confirmed_spec.get("io_allocation_raw"), str):
        result["io_allocation_raw"] = confirmed_spec["io_allocation_raw"]
    # These contracts have legacy scalar/list forms and device-keyed maps.
    # Keep their engineering meaning without opening arbitrary nested objects.
    _project_logic_contracts(confirmed_spec, result)
    if isinstance(confirmed_spec.get("logic_contracts"), Mapping):
        result["logic_contracts"] = {}
        _project_logic_contracts(confirmed_spec["logic_contracts"], result["logic_contracts"])
    return result


def _project_logic_contracts(source, target):
    if "mutex" in source:
        values = source["mutex"]
        if isinstance(values, (str, Mapping)):
            values = [values]
        if isinstance(values, list):
            target["mutex"] = [
                _project(value, _MUTEX_FIELDS if isinstance(value, Mapping) else [None] if isinstance(value, list) else None)
                for value in values
            ]
    if "same_scan_expectations" in source:
        values = source["same_scan_expectations"]
        if isinstance(values, Mapping):
            values = [values]
        target["same_scan_expectations"] = _project(values, [_EXPECTATION_FIELDS])
    if isinstance(source.get("terminal_states"), Mapping):
        target["terminal_states"] = {
            address: _project(value, [None] if isinstance(value, list) else None)
            for address, value in source["terminal_states"].items()
            if isinstance(address, str) and re.fullmatch(r"D\d+", address, re.IGNORECASE)
        }


def ladder_response_schema(*, allow_partial=False, plc_model=None):
    """Share the same generation schema with API prompts and external tools."""
    full = ladder_v1_schema(plc_model=plc_model)
    if not allow_partial:
        return full
    partial = _object({
        "mode": {"const": "partial"},
        "device_comments": copy.deepcopy(full["properties"]["device_comments"]),
        "rungs": _array(copy.deepcopy(full["properties"]["rungs"]["items"])),
        "delete_rung_ids": _array({"type": "integer", "minimum": 0}),
    }, ["mode"])
    return {"oneOf": [full, partial]}
