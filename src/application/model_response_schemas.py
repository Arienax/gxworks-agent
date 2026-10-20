"""Provider response schemas constrained by the current repair baseline."""
import json
from plc.generation_contract import ladder_response_schema

def _repair_baseline_tokens(repair_payload):
    """Collect immutable engineering tokens already present in the repair slice."""
    result = {"addresses": set(), "operands": set(), "values": set(),
              "expressions": set(), "app_instr_arities": set(),
              "app_instr_instances": []}

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
                    opcode = str(value.get("opcode") or "").strip().upper()
                    if opcode:
                        instance = {"opcode": opcode, "operands": list(tokens)}
                        if instance not in result["app_instr_instances"]:
                            result["app_instr_instances"].append(instance)
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
    app_instr_instances = list(tokens["app_instr_instances"])
    baseline_opcodes = sorted({item["opcode"] for item in app_instr_instances})

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
                if isinstance(opcode_rule, dict):
                    # Structural repair is copy-only: the model may reuse only
                    # APP_INSTR opcodes already present in baseline_subset.
                    opcode_rule["enum"] = baseline_opcodes
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


def _native_field_patch_response_format(repair_payload):
    target = repair_payload.get("target") if isinstance(repair_payload, dict) else None
    if not isinstance(target, dict) or not isinstance(target.get("path"), str):
        raise ValueError("field repair target is required")
    value_schema = json.loads(json.dumps(target.get("value_schema") or {}))
    if not value_schema:
        raise ValueError("field repair value schema is required")
    base_sha = str(repair_payload.get("base_sha256") or "")
    schema = {
        "type": "object",
        "properties": {
            "schema_version": {"type": "integer", "enum": [1]},
            "mode": {"type": "string", "enum": ["field_patch"]},
            "base_sha256": {"type": "string", "enum": [base_sha]},
            "patches": {
                "type": "array", "minItems": 1, "maxItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "enum": [target["path"]]},
                        "value": value_schema,
                    },
                    "required": ["path", "value"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["schema_version", "mode", "base_sha256", "patches"],
        "additionalProperties": False,
    }
    return {"type": "json_schema", "json_schema": {
        "name": "ladder_field_patch", "strict": True, "schema": schema,
    }}


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
            address: {"type": "string"}
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
