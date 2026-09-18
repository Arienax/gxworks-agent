"""Model suggestions do not establish hardware or add irrelevant mandatory work."""
import copy
import json

import pytest

from api import _normalize_analysis_result
from confirmed_spec import build_review_draft, canonicalize_confirmed_spec, validate_spec_draft
from hardware_profiles import ensure_hardware_questions, hardware_requirement_flags


def hallucinated_analysis():
    return {
        "summary": "起保停控制变频器运行。",
        "control_type": ["启停"],
        "approaches": [
            {"approach_id": "direct", "name": "直接起保停", "generation_contract": {
                "required_structures": ["direct_logic", "self_hold"],
                "forbidden_structures": ["vfd_multi_speed", "analog_control", "serial_communication"]}},
            {"approach_id": "guessed_drive", "name": "变频器控制", "generation_contract": {
                "required_structures": ["vfd_multi_speed"]}},
        ],
        "missing_info": [
            {"id": "control_method", "question": "变频器频率给定控制方式", "required": True,
             "options": ["多段速", "模拟量", "Modbus"]},
            {"id": "drive_model", "question": "变频器型号", "required": True},
            {"id": "register", "question": "寄存器地址", "required": True,
             "required_when": {"parameter": "control_method", "contains": "Modbus"}},
            {"id": "start_input", "question": "启动输入接哪个X？", "required": True, "options": ["X000"]},
            {"id": "stop_input", "question": "停止输入接哪个X？", "required": True, "options": ["X001"]},
            {"id": "output_address", "question": "电机接触器输出接哪个Y？", "required": True, "options": ["Y000"]},
        ],
        "suggested_io": {"X": {"X000": "启动按钮", "X001": "停止按钮"}, "Y": {"Y000": "电机接触器输出"}},
        "hardware_config": {"drive": {"model": "假设的变频器"}},
        "hardware_requirements": {"vfd": True, "analog": True, "serial": True},
        "assumptions": ["候选可使用变频器"],
    }


@pytest.mark.parametrize("requirement", ["起保停", "普通电机起停", "X0启动 X1停止 Y0电机", "正反转接触器控制", "延时启动", "星三角接触器启动",
                                     "Simple start hold stop", "普通启停，不使用变频器", "Motor start without an inverter", "インバータ不要、起動停止"])
def test_plain_requirement_never_gains_vfd_from_model_prose_questions_or_contracts(requirement):
    raw = hallucinated_analysis()
    before = copy.deepcopy(raw)
    normalized = _normalize_analysis_result(raw, "FX3U", requirement)
    assert raw == before
    assert not normalized["hardware_requirements"]["vfd"]
    assert [q["id"] for q in normalized["missing_info"]] == ["start_input", "stop_input", "output_address"]
    assert len(normalized["approaches"]) == 1
    assert normalized["approaches"][0]["generation_contract"]["forbidden_structures"]
    assert "假设的变频器" not in json.dumps(normalized.get("hardware_config", {}), ensure_ascii=False)
    for _ in range(3):
        normalized = ensure_hardware_questions(normalized)
        assert not normalized["hardware_requirements"]["vfd"]
        assert len(normalized["missing_info"]) == 3
    draft = build_review_draft(normalized)
    for parameter, address in zip(draft["parameters"], ["X000", "X001", "Y000"]):
        parameter.update(value=address, source="user")
    assert validate_spec_draft(draft)["errors"] == []
    saved = canonicalize_confirmed_spec(draft)
    assert {r["address"] for r in saved["io_table"]} == {"X0", "X1", "Y0"}
    assert not saved["hardware_requirements"]["vfd"]


@pytest.mark.parametrize("requirement", ["变频器起停", "变频器20/50/60Hz运行", "VFD speed control", "电机20/50/60Hz运行"])
def test_genuine_drive_still_gets_a_command_interface_question(requirement):
    result = ensure_hardware_questions({"summary": "控制", "missing_info": []}, user_text=requirement)
    assert result["hardware_requirements"]["vfd"]
    assert [q["id"] for q in result["missing_info"]] == ["control_method"]
    draft = build_review_draft(result)
    assert draft["parameters"][0]["required"] is True
    assert draft["parameters"][0]["value"] == ""


@pytest.mark.parametrize("method", ["Modbus", "模拟量", "STF多段速"])
def test_a_chosen_interface_is_retained_without_another_mandatory_choice(method):
    request = f"采用{method}控制变频器"
    normalized = _normalize_analysis_result(hallucinated_analysis(), "FX3U", request)
    draft = build_review_draft(normalized)
    parameter = next(p for p in draft["parameters"] if p["id"] == "control_method")
    assert not parameter["required"]
    assert method in parameter["value"] or method == "STF多段速" and "STF" in parameter["value"]
    assert parameter["source"] == "confirmed_request_fact"
    # Register dependencies see an actual resolved controller, not an absent key.
    assert bool(parameter["value"])


def test_user_list_of_alternatives_is_not_a_chosen_interface():
    result = ensure_hardware_questions({"missing_info": []}, user_text="变频器用模拟量还是Modbus？")
    assert not result["hardware_intent"]["vfd_method_selected"]
    assert result["missing_info"][0]["required"]


def test_previous_confirmed_vfd_survives_an_unrelated_current_edit():
    previous = {"parameters": [{"id": "control_method", "name": "变频器给定方式", "value": "Modbus", "source": "user"}],
                "io_table": [{"kind": "X", "address": "X0", "label": "启动"}]}
    normalized = _normalize_analysis_result(hallucinated_analysis(), "FX3U", "把延时改成3秒", previous)
    assert normalized["hardware_requirements"]["vfd"]
    draft = build_review_draft(normalized, previous)
    assert next(p for p in draft["parameters"] if p["id"] == "control_method")["value"] == "Modbus"


def test_a_current_denial_does_not_turn_back_into_vfd_during_review():
    previous = {"parameters": [{"id": "control_method", "name": "控制方式", "value": "Modbus"}]}
    result = _normalize_analysis_result(hallucinated_analysis(), "FX3U", "不用变频器，改为普通接触器起保停", previous)
    assert not result["hardware_requirements"]["vfd"]
    draft = build_review_draft(result, previous)
    assert not draft["hardware_requirements"]["vfd"]
    assert not any(p["id"] == "control_method" for p in draft["parameters"])
    assert not draft["hardware_profile"]["control_method"]


def test_unknown_legacy_flags_do_not_self_generate_required_hardware():
    raw = {"hardware_requirements": {"vfd": True, "analog": True, "serial": True}, "missing_info": []}
    for _ in range(3):
        raw = ensure_hardware_questions(raw)
        assert raw["missing_info"] == []
        assert raw["hardware_intent"]["source"] == "legacy_unknown"


def test_actual_motion_and_independent_analog_questions_survive_vfd_cleanup():
    raw = hallucinated_analysis()
    raw["missing_info"] += [{"id": "pulse_output_axis", "question": "伺服脉冲输出轴", "required": True},
                            {"id": "sensor_channel", "question": "模拟量压力传感器通道", "required": True}]
    result = ensure_hardware_questions(raw, user_text="伺服定位，并读取模拟量压力传感器")
    assert {q["id"] for q in result["missing_info"]} >= {"pulse_output_axis", "sensor_channel"}
    assert "control_method" not in {q["id"] for q in result["missing_info"]}


def test_raw_agent_cannot_claim_a_confirmed_hardware_evidence_record():
    raw = hallucinated_analysis()
    raw["hardware_intent"] = {"version": 1, "source": "user_request", "flags": {"vfd": True}, "vfd_method_value": "Modbus"}
    result = _normalize_analysis_result(raw, "FX3U", "起保停")
    assert not result["hardware_requirements"]["vfd"]
    assert not result["hardware_intent"]["vfd_method_value"]
