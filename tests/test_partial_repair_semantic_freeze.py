import json
from types import SimpleNamespace as S

import application.model_workflows as api


def payload():
    return {
        "plc_model": "FX3U",
        "allowed_rung_ids": [1],
        "allowed_addresses": ["D0", "D10", "M0"],
        "baseline_subset": {
            "device_comments": {},
            "rungs": [{
                "rung_id": 1, "header_element": None, "shared_inputs": [],
                "branches": [{
                    "branch_id": 1, "y_offset_level": 0, "inputs": [],
                    "outputs": [
                        {"type": "APP_INSTR", "opcode": "DMOVP", "operands": ["D0", "D10"], "label": None},
                        {"type": "APP_INSTR", "opcode": "MOVP", "operands": ["K1", "D0"], "label": None},
                        {"type": "APP_INSTR", "opcode": "RST", "operands": ["M0"], "label": None},
                    ],
                }],
            }],
        },
    }


def app_rule(native_format):
    schema = native_format["json_schema"]["schema"]
    return (schema["properties"]["rungs"]["items"]["properties"]["branches"]["items"]
            ["properties"]["outputs"]["items"]["oneOf"][2])


def test_partial_repair_schema_reuses_only_baseline_opcodes():
    rule = app_rule(api._native_partial_repair_response_format(payload()))
    assert rule["properties"]["opcode"]["enum"] == ["DMOVP", "MOVP", "RST"]
    assert "DDADDP" not in rule["properties"]["opcode"]["enum"]
    assert "DADDP" not in rule["properties"]["opcode"]["enum"]


def test_repair_contract_exposes_exact_immutable_instruction_instances(monkeypatch):
    captured = {}
    def fake_request(messages, **kwargs):
        captured["messages"] = messages
        captured["options"] = kwargs.get("options")
        return S(message=S(reasoning="", content='{"mode":"partial","device_comments":{},"rungs":[],"delete_rung_ids":[]}'))
    monkeypatch.setattr(api, "_request_model", fake_request)
    api.repair_ladder_response(payload(), "offline", "low", mode="partial")
    sent = json.loads(captured["messages"][1]["content"])
    contract = sent["repair_contract"]
    assert contract["semantic_policy"] == "copy_only"
    assert contract["app_instr_opcode_enum"] == ["DMOVP", "MOVP", "RST"]
    assert contract["app_instr_instances"] == [
        {"opcode": "DMOVP", "operands": ["D0", "D10"]},
        {"opcode": "MOVP", "operands": ["K1", "D0"]},
        {"opcode": "RST", "operands": ["M0"]},
    ]
    assert "DDADDP" not in sent["repair_contract"]["app_instr_opcode_enum"]
    assert "copy BOTH" in captured["messages"][0]["content"]


def test_partial_repair_without_app_instr_makes_app_instr_schema_unsatisfiable():
    value = payload()
    value["baseline_subset"]["rungs"][0]["branches"][0]["outputs"] = [
        {"type": "COIL", "address": "M0", "label": None}
    ]
    rule = app_rule(api._native_partial_repair_response_format(value))
    assert rule["properties"]["opcode"]["enum"] == []
