import application.model_workflows as api
from plc.candidate_repair import validation_diagnostic
from plc.validation import PLCJsonValidationError


def _walk(rule):
    if isinstance(rule, dict):
        yield rule
        for value in rule.values():
            yield from _walk(value)
    elif isinstance(rule, list):
        for value in rule:
            yield from _walk(value)


def _payload():
    rung = {
        "rung_id": 7,
        "header_element": None,
        "shared_inputs": [{"type": "NO", "address": "X0", "label": None}],
        "branches": [{
            "branch_id": 1,
            "y_offset_level": 0,
            "inputs": [{"type": "NO", "address": "M0", "label": None}],
            "outputs": [{
                "type": "APP_INSTR", "opcode": "MOV",
                "operands": ["D0", "D1"], "label": None,
            }],
        }],
    }
    return {
        "repair_mode": "partial",
        "plc_model": "FX3U",
        "instruction": "repair one rung",
        "allowed_rung_ids": [7],
        "allowed_addresses": ["X0", "M0", "D0", "D1"],
        "baseline_subset": {"device_comments": {}, "rungs": [rung]},
    }


def test_native_repair_schema_reuses_only_baseline_devices_and_operands():
    native = api._native_partial_repair_response_format(_payload())
    assert native["type"] == "json_schema"
    schema = native["json_schema"]["schema"]
    address_enums = []
    operand_enums = []
    opcode_enums = []
    for rule in _walk(schema):
        properties = rule.get("properties") if isinstance(rule, dict) else None
        if not isinstance(properties, dict):
            continue
        if isinstance(properties.get("address"), dict) and "enum" in properties["address"]:
            address_enums.append(set(properties["address"]["enum"]))
        type_rule = properties.get("type")
        if isinstance(type_rule, dict) and "APP_INSTR" in type_rule.get("enum", []):
            opcode_enums.append(set(properties["opcode"]["enum"]))
            operand_enums.append(set(properties["operands"]["items"]["enum"]))
            assert properties["operands"]["minItems"] == 2
            assert properties["operands"]["maxItems"] == 2
    assert address_enums
    assert all(items <= {"X0", "M0", "D0", "D1"} for items in address_enums)
    assert operand_enums == [{"D0", "D1"}]
    assert opcode_enums and "MOV" in opcode_enums[0]
    assert opcode_enums[0] <= set(api.generation_app_instr_mnemonics("FX3U"))
    assert "OUT" not in opcode_enums[0]


def test_native_repair_schema_preserves_baseline_values_and_expressions():
    payload = _payload()
    rung = payload["baseline_subset"]["rungs"][0]
    rung["header_element"] = {"type": "COMPARE", "expression": "> D0 K10", "label": None}
    rung["branches"][0]["outputs"].append(
        {"type": "TIMER", "address": "T0", "value": "K5", "label": None}
    )
    payload["allowed_addresses"].append("T0")
    schema = api._native_partial_repair_response_format(payload)["json_schema"]["schema"]
    expression_enums, value_enums = [], []
    for rule in _walk(schema):
        properties = rule.get("properties") if isinstance(rule, dict) else None
        if not isinstance(properties, dict):
            continue
        type_rule = properties.get("type")
        type_values = set(type_rule.get("enum", [])) if isinstance(type_rule, dict) else set()
        if type_values & {"COMPARE", "BLOCK_INPUT"}:
            if isinstance(properties.get("expression"), dict) and "enum" in properties["expression"]:
                expression_enums.append(set(properties["expression"]["enum"]))
        if type_values & {"TIMER", "COUNTER"}:
            if isinstance(properties.get("value"), dict) and "enum" in properties["value"]:
                value_enums.append(set(properties["value"]["enum"]))
    assert expression_enums and all(items == {"> D0 K10"} for items in expression_enums)
    assert value_enums and all(items == {"K5"} for items in value_enums)


def test_scope_violation_diagnostic_is_not_hidden_as_generic_structure_error():
    diagnostic = validation_diagnostic(
        PLCJsonValidationError("$.rungs: contract repair introduced out-of-scope devices D9")
    )
    assert diagnostic == {"path": "content$.rungs", "reason": "repair_scope_violation"}
