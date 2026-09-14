"""Narrow model boundary for the rare whole-rung structural repair path.

Only representation/container errors reach this module. The prompt and native
schema preserve baseline engineering tokens; deterministic validation performs
an independent semantic-token equality check before merge.
"""
from __future__ import annotations

import json


STRUCTURAL_REPAIR_SYSTEM_PROMPT = """# PLC ladder structural representation repair
Repair only the representation/container error in the supplied existing rung.
Do not infer PLC intent and do not replace any engineering value.

Return one pure JSON object in partial-edit form:
{"mode":"partial","device_comments":{},"rungs":[],"delete_rung_ids":[]}

Hard rules:
- Only rung_id values in allowed_rung_ids may appear; never add/delete/renumber rungs.
- Preserve every leaf element's type/polarity, address, expression, APP_INSTR opcode,
  operands, timer/counter preset, and output target exactly as baseline_subset.
- You may only move/re-nest existing elements between shared_inputs/branches/inputs,
  repair branch container shape, and renumber branch_id/y_offset_level to match order.
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


def structural_repair_response(repair_payload, model_name, effort, *, mode="partial",
                               on_reasoning_chunk=None, on_content_chunk=None):
    """Call the model only for a structure-only rung rewrite with frozen semantics."""
    if mode != "partial" or not isinstance(repair_payload, dict):
        raise ValueError("structural repair requires a partial repair payload")
    import api

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
    return response.message.reasoning, response.message.content
