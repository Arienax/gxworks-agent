#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new, label):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# api.py: import the shared registry object so native schema can filter opcodes
# by the operand counts already present in the immutable repair baseline.
replace_once(
    "src/api.py",
    "from instruction_registry import GENERATION_TYPED_OUTPUT_OPCODES, generation_app_instr_mnemonics\n",
    "from instruction_registry import (\n    DEFAULT_INSTRUCTION_REGISTRY, GENERATION_TYPED_OUTPUT_OPCODES,\n    generation_app_instr_mnemonics,\n)\n",
    "instruction registry import",
)

old = '''def _native_partial_repair_response_format(repair_payload):
    """Build the provider-enforced partial repair schema from the shared ladder contract."""
    plc_model = str(repair_payload.get("plc_model") or "FX3U").strip().upper() or "FX3U"
    combined = ladder_response_schema(allow_partial=True, plc_model=plc_model)
    schema = combined["oneOf"][1]
    schema["required"] = ["mode", "device_comments", "rungs", "delete_rung_ids"]
    schema["properties"]["delete_rung_ids"]["maxItems"] = 0

    allowed_rung_ids = sorted({
        int(item) for item in (repair_payload.get("allowed_rung_ids") or [])
        if not isinstance(item, bool)
    })
    rung_array = schema["properties"]["rungs"]
    if allowed_rung_ids:
        rung_array["minItems"] = 1
        rung_array["maxItems"] = len(allowed_rung_ids)
        rung_array["items"]["properties"]["rung_id"] = {
            "type": "integer", "enum": allowed_rung_ids,
        }
        # Structural rung repair must not opportunistically rewrite comments.
        schema["properties"]["device_comments"] = {
            "type": "object", "properties": {}, "required": [],
            "additionalProperties": False,
        }
    else:
        rung_array["maxItems"] = 0
        allowed_addresses = sorted({
            str(item).strip().upper() for item in (repair_payload.get("allowed_addresses") or [])
            if str(item).strip()
        })
        comment_properties = {
            address: {"type": "string", "maxLength": 64}
            for address in allowed_addresses
        }
        schema["properties"]["device_comments"] = {
            "type": "object",
            "properties": comment_properties,
            "required": allowed_addresses,
            "additionalProperties": False,
        }

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ladder_partial_repair",
            "strict": True,
            "schema": schema,
        },
    }
'''

new = '''def _repair_baseline_tokens(repair_payload):
    """Collect immutable engineering tokens already present in the repair slice."""
    result = {"addresses": set(), "operands": set(), "values": set(),
              "expressions": set(), "app_instr_arities": set()}

    def walk(value):
        if isinstance(value, dict):
            address = value.get("address")
            if isinstance(address, str) and address.strip():
                result["addresses"].add(address.strip().upper())
            expression = value.get("expression")
            if isinstance(expression, str) and expression.strip():
                result["expressions"].add(expression.strip())
            preset = value.get("value")
            if isinstance(preset, str) and preset.strip():
                result["values"].add(preset.strip())
            operands = value.get("operands")
            if isinstance(operands, list):
                tokens = [str(item).strip() for item in operands if isinstance(item, str) and str(item).strip()]
                result["operands"].update(tokens)
                if value.get("type") == "APP_INSTR":
                    result["app_instr_arities"].add(len(operands))
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(repair_payload.get("baseline_subset") or {})
    result["addresses"].update(
        str(item).strip().upper()
        for item in (repair_payload.get("allowed_addresses") or [])
        if str(item).strip()
    )
    return result


def _constrain_native_repair_schema(schema, repair_payload, plc_model):
    """Prevent a local repair from inventing devices/parameters outside its baseline."""
    tokens = _repair_baseline_tokens(repair_payload)
    addresses = sorted(tokens["addresses"])
    operands = sorted(tokens["operands"])
    values = sorted(tokens["values"])
    expressions = sorted(tokens["expressions"])
    arities = sorted(tokens["app_instr_arities"])

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
            if "address" in properties and addresses:
                properties["address"]["enum"] = addresses
            if "expression" in properties and expressions:
                properties["expression"]["enum"] = expressions
            if "value" in properties and values:
                properties["value"]["enum"] = values
            if "APP_INSTR" in type_values:
                opcode_rule = properties.get("opcode")
                operand_rule = properties.get("operands")
                if isinstance(opcode_rule, dict) and arities:
                    compatible = []
                    for mnemonic in generation_app_instr_mnemonics(plc_model):
                        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(mnemonic)
                        if spec is not None and any(spec.accepts_arity(count) for count in arities):
                            compatible.append(mnemonic)
                    if compatible:
                        opcode_rule["enum"] = compatible
                if isinstance(operand_rule, dict):
                    if operands:
                        # ladder_v1_schema reuses the generic token rule for
                        # TIMER/COUNTER values and APP_INSTR operands. Detach
                        # the operand item rule before adding an enum so this
                        # local repair constraint cannot mutate timer presets.
                        operand_rule["items"] = dict(operand_rule.get("items") or {})
                        operand_rule["items"]["enum"] = operands
                    if len(arities) == 1:
                        operand_rule["minItems"] = arities[0]
                        operand_rule["maxItems"] = arities[0]
            for child in properties.values():
                visit(child)
        for key in ("oneOf", "anyOf", "allOf"):
            visit(rule.get(key))
        visit(rule.get("items"))

    visit(schema)
    return schema


def _native_partial_repair_response_format(repair_payload):
    """Build the provider-enforced partial repair schema from the shared ladder contract."""
    plc_model = str(repair_payload.get("plc_model") or "FX3U").strip().upper() or "FX3U"
    combined = ladder_response_schema(allow_partial=True, plc_model=plc_model)
    schema = combined["oneOf"][1]
    schema["required"] = ["mode", "device_comments", "rungs", "delete_rung_ids"]
    schema["properties"]["delete_rung_ids"]["maxItems"] = 0

    allowed_rung_ids = sorted({
        int(item) for item in (repair_payload.get("allowed_rung_ids") or [])
        if not isinstance(item, bool)
    })
    rung_array = schema["properties"]["rungs"]
    if allowed_rung_ids:
        rung_array["minItems"] = 1
        rung_array["maxItems"] = len(allowed_rung_ids)
        rung_array["items"]["properties"]["rung_id"] = {
            "type": "integer", "enum": allowed_rung_ids,
        }
        # Structural rung repair must not opportunistically rewrite comments.
        schema["properties"]["device_comments"] = {
            "type": "object", "properties": {}, "required": [],
            "additionalProperties": False,
        }
        _constrain_native_repair_schema(schema, repair_payload, plc_model)
    else:
        rung_array["maxItems"] = 0
        allowed_addresses = sorted({
            str(item).strip().upper() for item in (repair_payload.get("allowed_addresses") or [])
            if str(item).strip()
        })
        comment_properties = {
            address: {"type": "string", "maxLength": 64}
            for address in allowed_addresses
        }
        schema["properties"]["device_comments"] = {
            "type": "object",
            "properties": comment_properties,
            "required": allowed_addresses,
            "additionalProperties": False,
        }

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ladder_partial_repair",
            "strict": True,
            "schema": schema,
        },
    }
'''
replace_once("src/api.py", old, new, "native repair schema block")

# Diagnostics: make repair scope failures visible instead of collapsing them
# into generic invalid_ladder_structure.
replace_once(
    "src/application/generation_repair.py",
    '              "invalid_shared_input" if "shared_inputs" in safe and "parallel_block" in text else\n              "field_too_long" if ("must be <=" in text or "invalid text length" in text) else\n              "invalid_ladder_structure")\n',
    '              "invalid_shared_input" if "shared_inputs" in safe and "parallel_block" in text else\n              "field_too_long" if ("must be <=" in text or "invalid text length" in text) else\n              "repair_scope_violation" if ("evidence-external" in text or "out-of-scope" in text) else\n              "invalid_ladder_structure")\n',
    "repair scope diagnostic",
)

Path("tests/test_native_repair_scope_schema.py").write_text(r'''import api
from application.generation_repair import validation_diagnostic
from plc_json_validator import PLCJsonValidationError


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
''', encoding="utf-8")
