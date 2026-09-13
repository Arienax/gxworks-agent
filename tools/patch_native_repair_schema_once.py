#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new, label):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/api.py",
    "from instruction_registry import GENERATION_TYPED_OUTPUT_OPCODES, generation_app_instr_mnemonics\n",
    "from instruction_registry import GENERATION_TYPED_OUTPUT_OPCODES, generation_app_instr_mnemonics\n"
    "from plc_generation_contract import ladder_response_schema\n",
    "api repair schema import",
)

anchor = '''\n\n@language_scoped\ndef repair_ladder_response(repair_payload, model_name, effort, *, mode,\n                           on_reasoning_chunk=None, on_content_chunk=None):\n'''
helper = '''\n\ndef _native_partial_repair_response_format(repair_payload):\n    """Build the provider-enforced partial repair schema from the shared ladder contract."""\n    plc_model = str(repair_payload.get("plc_model") or "FX3U").strip().upper() or "FX3U"\n    combined = ladder_response_schema(allow_partial=True, plc_model=plc_model)\n    schema = combined["oneOf"][1]\n    schema["required"] = ["mode", "device_comments", "rungs", "delete_rung_ids"]\n    schema["properties"]["delete_rung_ids"]["maxItems"] = 0\n\n    allowed_rung_ids = sorted({\n        int(item) for item in (repair_payload.get("allowed_rung_ids") or [])\n        if not isinstance(item, bool)\n    })\n    rung_array = schema["properties"]["rungs"]\n    if allowed_rung_ids:\n        rung_array["minItems"] = 1\n        rung_array["maxItems"] = len(allowed_rung_ids)\n        rung_array["items"]["properties"]["rung_id"] = {\n            "type": "integer", "enum": allowed_rung_ids,\n        }\n        # Structural rung repair must not opportunistically rewrite comments.\n        schema["properties"]["device_comments"] = {\n            "type": "object", "properties": {}, "required": [],\n            "additionalProperties": False,\n        }\n    else:\n        rung_array["maxItems"] = 0\n        allowed_addresses = sorted({\n            str(item).strip().upper() for item in (repair_payload.get("allowed_addresses") or [])\n            if str(item).strip()\n        })\n        comment_properties = {\n            address: {"type": "string", "maxLength": 64}\n            for address in allowed_addresses\n        }\n        schema["properties"]["device_comments"] = {\n            "type": "object",\n            "properties": comment_properties,\n            "required": allowed_addresses,\n            "additionalProperties": False,\n        }\n\n    return {\n        "type": "json_schema",\n        "json_schema": {\n            "name": "ladder_partial_repair",\n            "strict": True,\n            "schema": schema,\n        },\n    }\n\n\n@language_scoped\ndef repair_ladder_response(repair_payload, model_name, effort, *, mode,\n                           on_reasoning_chunk=None, on_content_chunk=None):\n'''
replace_once("src/api.py", anchor, helper, "native repair schema helper")

replace_once(
    "src/api.py",
    '''    system_prompt = (PARTIAL_LADDER_REPAIR_SYSTEM_PROMPT\n                     if mode == "partial" else FORMAT_LADDER_REPAIR_SYSTEM_PROMPT)\n''',
    '''    native_response_format = (\n        _native_partial_repair_response_format(repair_payload)\n        if mode == "partial" else None\n    )\n    system_prompt = (PARTIAL_LADDER_REPAIR_SYSTEM_PROMPT\n                     if mode == "partial" else FORMAT_LADDER_REPAIR_SYSTEM_PROMPT)\n''',
    "repair native response format selection",
)

replace_once(
    "src/api.py",
    '''        response_contract=LADDER_RESPONSE,\n        preserved_annotations=source_annotations(repair_payload),\n''',
    '''        options={"response_format": native_response_format} if native_response_format else None,\n        response_contract=LADDER_RESPONSE,\n        preserved_annotations=source_annotations(repair_payload),\n''',
    "repair native response format request",
)

# Extend the real workbench repair regression so the provider-facing ModelRequest
# must carry native json_schema, not merely a prompt-side enum.
test_path = Path("tests/test_user_confirmed_generation_repair.py")
text = test_path.read_text(encoding="utf-8")
old = '''        repair_payload = json.loads(str(provider.requests[1].messages[-1].content))\n        assert "PLC ladder local structural repair" in system_prompt\n'''
new = '''        repair_payload = json.loads(str(provider.requests[1].messages[-1].content))\n        native_format = provider.requests[1].options["response_format"]\n        assert native_format["type"] == "json_schema"\n        assert native_format["json_schema"]["strict"] is True\n        native_schema = native_format["json_schema"]["schema"]\n        assert native_schema["properties"]["rungs"]["items"]["properties"]["rung_id"]["enum"] == [1]\n        opcode_rule = (\n            native_schema["properties"]["rungs"]["items"]["properties"]["branches"]["items"]\n            ["properties"]["outputs"]["items"]["oneOf"][2]["properties"]["opcode"]\n        )\n        assert opcode_rule["enum"] == repair_payload["repair_contract"]["app_instr_opcode_enum"]\n        assert native_schema["properties"]["device_comments"]["additionalProperties"] is False\n        assert native_schema["properties"]["delete_rung_ids"]["maxItems"] == 0\n        assert "PLC ladder local structural repair" in system_prompt\n'''
if text.count(old) != 1:
    raise SystemExit(f"repair request test anchor mismatch: {text.count(old)}")
test_path.write_text(text.replace(old, new, 1), encoding="utf-8")
