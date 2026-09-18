"""Narrow model boundary for the rare whole-rung structural repair path.

Only the explicitly-recognized shared-input representation error reaches this
module. The model may relocate containers, but branch behavior is compared
against the immutable baseline before the response is accepted.
"""
from __future__ import annotations

import copy
import json


STRUCTURAL_REPAIR_SYSTEM_PROMPT = """# PLC ladder structural representation repair
Repair only the representation/container error in the supplied existing rung.
Do not infer PLC intent and do not replace any engineering value.

Return one pure JSON object in partial-edit form:
{"mode":"partial","device_comments":{},"rungs":[],"delete_rung_ids":[]}

Hard rules:
- Only rung_id values in allowed_rung_ids may appear; never add/delete/renumber rungs.
- Preserve every branch's effective condition-to-output relationship exactly.
- Preserve every element type/polarity, address, expression, APP_INSTR opcode,
  operands, timer/counter preset, output target, branch order and output order.
- To repair a parallel_block in shared_inputs, relocate existing conditions only;
  do not invent, remove, swap or reinterpret any condition or output.
- Do not choose a different opcode because it looks more plausible or compatible.
- device_comments must stay empty and delete_rung_ids must stay empty.
- Return JSON only; no explanation or markdown.
"""


def _baseline_opcodes(value):
    result = set()
    if isinstance(value, dict):
        if value.get("type") == "APP_INSTR":
            opcode = value.get("opcode")
            if isinstance(opcode, str) and opcode.strip():
                result.add(opcode.strip().upper())
        for nested in value.values():
            result.update(_baseline_opcodes(nested))
    elif isinstance(value, list):
        for nested in value:
            result.update(_baseline_opcodes(nested))
    return result


def _restrict_opcode_schema(native_format, opcodes):
    if not opcodes:
        return native_format
    schema = native_format.get("json_schema", {}).get("schema", {})

    def visit(rule):
        if isinstance(rule, list):
            for child in rule:
                visit(child)
            return
        if not isinstance(rule, dict):
            return
        properties = rule.get("properties")
        if isinstance(properties, dict):
            type_rule = properties.get("type")
            type_values = set(type_rule.get("enum", [])) if isinstance(type_rule, dict) else set()
            if "APP_INSTR" in type_values and isinstance(properties.get("opcode"), dict):
                properties["opcode"]["enum"] = sorted(opcodes)
            for child in properties.values():
                visit(child)
        for key in ("oneOf", "anyOf", "allOf"):
            visit(rule.get(key))
        visit(rule.get("items"))

    visit(schema)
    return native_format


def _element_semantics(element):
    if not isinstance(element, dict):
        return None
    if str(element.get("type") or "").casefold() == "parallel_block":
        return {
            "type": "parallel_block",
            "branches": [
                [_element_semantics(child) for child in branch]
                for branch in (element.get("branches") or [])
                if isinstance(branch, list)
            ],
        }
    return {
        key: copy.deepcopy(element[key])
        for key in ("type", "address", "expression", "opcode", "operands", "value")
        if key in element
    }


def _rung_behavior_signature(rung):
    """Normalize shared inputs as a prefix while preserving branch associations."""
    if not isinstance(rung, dict):
        return None
    shared = [
        _element_semantics(element)
        for element in (rung.get("shared_inputs") or [])
        if isinstance(element, dict)
    ]
    branches = []
    for branch in rung.get("branches", []) or []:
        if not isinstance(branch, dict):
            return None
        branches.append({
            "inputs": shared + [
                _element_semantics(element)
                for element in (branch.get("inputs") or [])
                if isinstance(element, dict)
            ],
            "outputs": [
                _element_semantics(element)
                for element in (branch.get("outputs") or [])
                if isinstance(element, dict)
            ],
        })
    return {
        "header_element": _element_semantics(rung.get("header_element")),
        "branches": branches,
    }


def _assert_structure_only_response(payload, content):
    """Reject token-preserving but behavior-changing whole-rung rewrites."""
    candidate = json.loads(str(content or "").strip())
    if not isinstance(candidate, dict):
        raise ValueError("structural repair response must be an object")
    baseline = {
        rung.get("rung_id"): rung
        for rung in ((payload.get("baseline_subset") or {}).get("rungs") or [])
        if isinstance(rung, dict) and isinstance(rung.get("rung_id"), int)
        and not isinstance(rung.get("rung_id"), bool)
    }
    for rung in candidate.get("rungs", []) or []:
        if not isinstance(rung, dict):
            raise ValueError("structural repair returned an invalid rung")
        rung_id = rung.get("rung_id")
        original = baseline.get(rung_id)
        if original is None:
            raise ValueError("structural repair returned an out-of-scope rung")
        if _rung_behavior_signature(original) != _rung_behavior_signature(rung):
            raise ValueError(
                f"structural repair changed condition/output behavior in rung {rung_id}"
            )


def structural_repair_response(repair_payload, model_name, effort, *, mode="partial",
                               on_reasoning_chunk=None, on_content_chunk=None):
    """Call the model only for a structure-only rung rewrite with frozen behavior."""
    if mode != "partial" or not isinstance(repair_payload, dict):
        raise ValueError("structural repair requires a partial repair payload")
    import application.model_workflows as api

    payload = dict(repair_payload)
    payload.pop("repair_contract", None)
    native = api._native_partial_repair_response_format(payload)
    opcodes = _baseline_opcodes(payload.get("baseline_subset") or {})
    native = _restrict_opcode_schema(native, opcodes)
    api.audit_section(
        "repair_system_prompt", STRUCTURAL_REPAIR_SYSTEM_PROMPT,
        reason="explicit_structural_repair", source="application.repair_policy",
    )
    messages = [
        {"role": "system", "content": STRUCTURAL_REPAIR_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]
    response = api._request_model(
        messages,
        model_name=model_name,
        effort=effort,
        stream=True,
        options={"response_format": native},
        response_contract=api.LADDER_RESPONSE,
        preserved_annotations=api.source_annotations(payload),
        on_reasoning_chunk=on_reasoning_chunk,
        on_content_chunk=on_content_chunk,
        fallback_to_non_stream=True,
    )
    _assert_structure_only_response(payload, response.message.content)
    return response.message.reasoning, response.message.content
