#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new, label):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


path = "tests/test_user_confirmed_generation_repair.py"

replace_once(path,
'''        else:\n            payload = {"mode": "partial", "device_comments": {},\n                       "rungs": [_ladder()["rungs"][0]], "delete_rung_ids": []}\n        raw = json.dumps(payload, ensure_ascii=False)\n''',
'''        else:\n            request_payload = json.loads(str(request.messages[-1].content))\n            target = request_payload["target"]\n            payload = {\n                "schema_version": 1, "mode": "field_patch",\n                "base_sha256": request_payload["base_sha256"],\n                "patches": [{"path": target["path"], "value": None}],\n            }\n        raw = json.dumps(payload, ensure_ascii=False)\n''',
"repair provider field response")

start = '''        assert repair_snapshot["repair_mode"] is True\n        assert repair_snapshot["format_repair"] is False\n        assert repair_snapshot["task_type"] == "contract_repair"\n        assert repair_snapshot["allowed_rung_ids"] == [1]\n        assert repair_snapshot["context_policy"]["name"] == "minimal"\n'''
replace_once(path, start,
'''        assert repair_snapshot["repair_mode"] is True\n        assert repair_snapshot["format_repair"] is False\n        assert repair_snapshot["task_type"] == "contract_repair"\n        assert repair_snapshot["repair_plan"]["mode"] == "field_patch"\n        assert repair_snapshot["repair_plan"]["target"]["path"] == "/rungs/0/debug_note"\n        assert repair_snapshot["allowed_rung_ids"] == []\n        assert repair_snapshot["context_policy"]["name"] == "minimal"\n''', "repair snapshot assertions")

old = '''        native_schema = native_format["json_schema"]["schema"]\n        assert native_schema["properties"]["rungs"]["items"]["properties"]["rung_id"]["enum"] == [1]\n        opcode_rule = (\n            native_schema["properties"]["rungs"]["items"]["properties"]["branches"]["items"]\n            ["properties"]["outputs"]["items"]["oneOf"][2]["properties"]["opcode"]\n        )\n        assert opcode_rule["enum"] == repair_payload["repair_contract"]["app_instr_opcode_enum"]\n        assert native_schema["properties"]["device_comments"]["additionalProperties"] is False\n        assert native_schema["properties"]["delete_rung_ids"]["maxItems"] == 0\n        assert "PLC ladder local structural repair" in system_prompt\n        assert "工业常识模式库" not in system_prompt\n        assert "Retrieved-knowledge precedence" not in system_prompt\n        assert len(system_prompt) < 5000\n        assert repair_payload["repair_mode"] == "partial"\n        contract = repair_payload["repair_contract"]\n        assert contract["plc_model"] == "FX3U"\n        assert "MOV" in contract["app_instr_opcode_enum"]\n        assert "NOT_A_REAL_OPCODE" not in contract["app_instr_opcode_enum"]\n        assert not set(contract["app_instr_forbidden_typed_opcodes"]) & set(contract["app_instr_opcode_enum"])\n        assert set(contract["app_instr_forbidden_typed_opcodes"]) == {"OUT", "PLS", "PLF", "END"}\n        assert len(json.dumps(contract, ensure_ascii=False, separators=(",", ":"))) < 3000\n        assert repair_payload["allowed_rung_ids"] == [1]\n        assert [r["rung_id"] for r in repair_payload["baseline_subset"]["rungs"]] == [1]\n        assert "用户明确确认的一次局部结构修复" in repair_payload["instruction"]\n        assert "失败候选 JSON" not in repair_payload["instruction"]\n'''
new = '''        native_schema = native_format["json_schema"]["schema"]\n        assert native_schema["properties"]["mode"]["enum"] == ["field_patch"]\n        assert native_schema["properties"]["base_sha256"]["enum"] == [repair_payload["base_sha256"]]\n        patch_schema = native_schema["properties"]["patches"]["items"]\n        assert patch_schema["properties"]["path"]["enum"] == ["/rungs/0/debug_note"]\n        assert patch_schema["properties"]["value"]["maxLength"] == 64\n        assert "rungs" not in native_schema["properties"]\n        assert "PLC ladder JSON field repair" in system_prompt\n        assert "工业常识模式库" not in system_prompt\n        assert "Retrieved-knowledge precedence" not in system_prompt\n        assert len(system_prompt) < 3000\n        assert repair_payload["repair_mode"] == "field_patch"\n        assert repair_payload["target"]["path"] == "/rungs/0/debug_note"\n        assert repair_payload["target"]["current_value"]\n        assert "baseline_subset" not in repair_payload\n        assert "用户明确确认的一次字段级 JSON 修复" in repair_payload["instruction"]\n        assert "失败候选 JSON" not in repair_payload["instruction"]\n'''
replace_once(path, old, new, "native field assertions")

replace_once(path,
'''        elif len(self.requests) == 2:\n            yield TextDelta('{"mode":"partial","device_comments":{},"rungs":[')\n        else:\n            yield TextDelta(json.dumps({"mode": "partial", "device_comments": {},\n                "rungs": [_ladder()["rungs"][0]], "delete_rung_ids": []}, ensure_ascii=False))\n''',
'''        elif len(self.requests) == 2:\n            yield TextDelta('{"schema_version":1,"mode":"field_patch","patches":[')\n        else:\n            request_payload = json.loads(str(request.messages[-1].content))\n            target = request_payload["target"]\n            yield TextDelta(json.dumps({\n                "schema_version": 1, "mode": "field_patch",\n                "base_sha256": request_payload["base_sha256"],\n                "patches": [{"path": target["path"], "value": None}],\n            }, ensure_ascii=False))\n''',
"retry provider field response")

replace_once(path,
'''        assert snapshot["repair_mode"] is True\n        assert snapshot["format_repair"] is False\n        assert snapshot["allowed_rung_ids"] == [1]\n        system_prompt = str(provider.requests[2].messages[0].content)\n        retry_payload = json.loads(str(provider.requests[2].messages[-1].content))\n        assert "PLC ladder local structural repair" in system_prompt\n        assert retry_payload["repair_mode"] == "partial"\n        assert retry_payload["allowed_rung_ids"] == [1]\n        assert "上一次局部修复回复仍未通过校验" in retry_payload["instruction"]\n        assert "上一次失败的局部 patch" in retry_payload["instruction"]\n''',
'''        assert snapshot["repair_mode"] is True\n        assert snapshot["format_repair"] is False\n        assert snapshot["repair_plan"]["mode"] == "field_patch"\n        assert snapshot["repair_plan"]["target"]["path"] == "/rungs/0/debug_note"\n        system_prompt = str(provider.requests[2].messages[0].content)\n        retry_payload = json.loads(str(provider.requests[2].messages[-1].content))\n        assert "PLC ladder JSON field repair" in system_prompt\n        assert retry_payload["repair_mode"] == "field_patch"\n        assert retry_payload["target"]["path"] == "/rungs/0/debug_note"\n''',
"retry field assertions")

replace_once(path,
'''            payload=_ladder()\n            payload["rungs"][0]["branches"][0]["outputs"]=[{"type":"APP_INSTR","opcode":"NOT_A_REAL_OPCODE","operands":["Y0"],"label":None}]\n            yield TextDelta(json.dumps(payload,ensure_ascii=False))\n        else:\n            yield TextDelta(json.dumps({"mode":"partial","device_comments":{},"rungs":[_ladder()["rungs"][0]],"delete_rung_ids":[]},ensure_ascii=False))\n''',
'''            payload=_ladder()\n            payload["rungs"][0]["branches"][0]["outputs"]=[{"type":"APP_INSTR","opcode":"NOT_A_REAL_OPCODE","operands":["D0","D1"],"label":None}]\n            yield TextDelta(json.dumps(payload,ensure_ascii=False))\n        else:\n            request_payload = json.loads(str(request.messages[-1].content))\n            target = request_payload["target"]\n            yield TextDelta(json.dumps({\n                "schema_version": 1, "mode": "field_patch",\n                "base_sha256": request_payload["base_sha256"],\n                "patches": [{"path": target["path"], "value": "MOV"}],\n            },ensure_ascii=False))\n''',
"format cascade field response")

replace_once(path,
'''        assert "PLC ladder local structural repair" in str(provider.requests[2].messages[0].content)\n        followup=json.loads(str(provider.requests[2].messages[-1].content))\n        assert followup["repair_mode"]=="partial"\n        assert "NOT_A_REAL_OPCODE" not in followup["repair_contract"]["app_instr_opcode_enum"]\n        assert "MOV" in followup["repair_contract"]["app_instr_opcode_enum"]\n        assert followup["allowed_rung_ids"]==[1]\n''',
'''        assert "PLC ladder JSON field repair" in str(provider.requests[2].messages[0].content)\n        followup=json.loads(str(provider.requests[2].messages[-1].content))\n        assert followup["repair_mode"]=="field_patch"\n        assert followup["target"]["path"]=="/rungs/0/branches/0/outputs/0/opcode"\n        assert "NOT_A_REAL_OPCODE" not in followup["target"]["value_schema"]["enum"]\n        assert "MOV" in followup["target"]["value_schema"]["enum"]\n''',
"format cascade field assertions")
