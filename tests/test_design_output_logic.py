"""Offline Design decisions and handoff; no provider or native PLC calls."""
import json

import pytest
from jsonschema import Draft202012Validator

from application.analysis_repair import (
    apply_analysis_repair, design_submission_schema, plan_analysis_repair,
)
from application.analysis_results import AnalysisProtocolError
from plc.specification.output_logic import output_logic_details


def contact_output():
    return [{
        "target": "Y0", "state": "无持久状态，读取 X0 当前值。",
        "activation": "X0 为真时 Y0 为真。", "update": "每扫描一次赋值，无多事件累积。",
        "release": "X0 为假时 Y0 为假，不需回收状态。",
        "interruption": "用户未要求暂停或恢复，不增加保持状态。",
        "selection": "采用直接组合逻辑；增加锁存会改变输入撤销后的输出，且增加状态和指令。",
    }]


def analysis(output=True):
    approach = {"approach_id": "contact", "name": "直接触点", "description": "X0 控制 Y0。",
                "generation_guide": "读取输入并赋值输出。", "implementation_semantics": [],
                "instruction_choices": [], "decision_basis": []}
    if output:
        approach["output_logic"] = contact_output()
    return {"summary": "X0 控制 Y0。", "approaches": [approach], "missing_info": []}


@pytest.mark.parametrize("value", [None, [], [{"target": "Y0", "update": "窗口控制"}],
                                   [{**contact_output()[0], "release": "  "}]])
def test_incomplete_output_is_repaired_before_draft_without_new_questions(value):
    payload = analysis(False)
    if value is not None:
        payload["approaches"][0]["output_logic"] = value
    plan = plan_analysis_repair(payload, "X0 控制 Y0。", require_output_logic=True)
    [target] = plan["targets"]
    assert target["path"] == "/approaches/0/output_logic"
    assert target["comparison"]["generation_guide"] == "读取输入并赋值输出。"
    repaired, _ = apply_analysis_repair(plan, {"repairs": [{"path": target["path"],
                                                          "value": contact_output()}]}, "X0 控制 Y0。")
    assert repaired == analysis()
    assert not plan_analysis_repair(repaired, "X0 控制 Y0。", require_output_logic=True)["targets"]
    with pytest.raises(AnalysisProtocolError):
        apply_analysis_repair(plan, {"repairs": [{"path": "/missing_info", "value": []}]}, "")


def test_legacy_plan_is_not_rewritten_or_rejected():
    payload = analysis(False)
    assert not output_logic_details(payload)
    assert not plan_analysis_repair(payload, "")["targets"]
    assert "output_logic" not in payload["approaches"][0]


@pytest.mark.parametrize("target,state,update,release", [
    ("电机", "运行状态位", "停止优先，按有效启停更新状态，统一驱动输出", "停止清除运行位"),
    ("状态机执行器", "当前步骤和完成标志", "按步骤 SET/RST，明确转换优先级", "离开步骤撤销该步骤输出"),
    ("批量采样结果", "当前采样批次和结果区", "采样完成时批量复制后再发布完成标志", "消费确认后允许替换结果"),
    ("运动命令", "当前命令和忙状态", "就绪时提交命令，忙期间不重复提交", "完成或故障后按已确认规则释放命令"),
])
def test_contract_supports_control_data_and_motion_outputs(target, state, update, release):
    payload = analysis()
    payload["approaches"][0]["output_logic"][0].update(
        target=target, state=state, update=update, release=release,
        interruption="恢复工艺未给定，保持未知；需依赖此条件的变体在 decision_basis 标为 conditional。",
        selection="已有资料仅支持此实现；未查到的替代写法保持未知，不宣称指令不支持。")
    Draft202012Validator(design_submission_schema()).validate(payload)
    assert not output_logic_details(payload, required=True)


def test_output_decisions_survive_confirmation_projection_and_followup():
    from plc.specification.confirmed import build_review_draft
    from plc.generation_contract import generation_specification
    from plc.specification.provenance import build_confirmed_spec
    payload = analysis()
    draft = build_review_draft(payload, None)
    draft["selected_approach"] = draft["approaches"][0]
    saved = build_confirmed_spec(draft)
    projected = generation_specification(saved)
    assert projected["selected_approach"]["output_logic"] == contact_output()
    assert not projected["selected_approach"]["generation_contract"]["required_opcodes"]
    following = build_review_draft(analysis(False), draft)
    assert following["approaches"][0]["output_logic"] == contact_output()
    assert following["parameters"] == []


def test_output_comparison_is_internal_and_not_a_specific_instruction_recipe():
    from application.prompts import ANALYSIS_OUTPUT_LOGIC_PROMPT, ANALYSIS_SYSTEM_PROMPT
    assert "Design 的方案选择在 approaches 中交给用户" in ANALYSIS_SYSTEM_PROMPT
    assert "该结构内部有实质差异的输出实现" in ANALYSIS_OUTPUT_LOGIC_PROMPT
    assert "状态存储、重复代码和每扫描处理量" in ANALYSIS_OUTPUT_LOGIC_PROMPT
    assert "未给定的工艺条件保持未知" in ANALYSIS_OUTPUT_LOGIC_PROMPT
    assert all(op not in ANALYSIS_OUTPUT_LOGIC_PROMPT for op in ("SFTL", "SFRD", "SFWR", "100ms"))


def test_nested_applicability_repair_has_condition_schema_not_decision_schema():
    from application.analysis_repair import _field_schema
    schema = _field_schema(["approaches", 0, "decision_basis", 0, "applicability_conditions", 0])
    condition = {"premise": "队列容量尚未说明", "status": "unknown", "references": []}
    Draft202012Validator(schema).validate(condition)
    assert set(schema["required"]) == {"premise", "status", "references"}

    payload = analysis()
    payload["approaches"][0]["decision_basis"] = [{
        "variant": "固定容量实现", "premise": "容量未定", "status": "unknown", "conclusion": "conditional",
        "references": [], "applicability_conditions": [{
            "premise": "容量为八", "status": "known", "references": []}],
    }]
    plan = plan_analysis_repair(payload, "X0 控制 Y0。", require_output_logic=True)
    [target] = plan["targets"]
    assert target["path"] == "/approaches/0/decision_basis/0/applicability_conditions/0"
    repaired, _ = apply_analysis_repair(plan, {"repairs": [{"path": target["path"],
        "value": {"status": "unknown"}}]}, "")
    assert not plan_analysis_repair(repaired, "", require_output_logic=True)["targets"]
    assert repaired["approaches"][0]["output_logic"] == contact_output()


def test_web_analysis_repairs_output_in_original_conversation(monkeypatch, tmp_path):
    import application.model_api as api
    from application.conversation_model import ModelTurn, model_turn_scope
    from test_analysis_format_repair import Provider
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **kw: "已有触点与线圈资料")
    patch = {"repairs": [{"path": "/approaches/0/output_logic", "value": contact_output()}]}
    provider = Provider(json.dumps(analysis(False), ensure_ascii=False), json.dumps(patch, ensure_ascii=False))
    turn = ModelTurn(tmp_path, (), {"model": "offline"}, {}, "zh-CN")
    with api.provider_scope(provider), model_turn_scope(turn):
        result = api.analyze_requirement_streaming("X0 控制 Y0。", analysis_mode="design")
    assert len(provider.requests) == 2
    first, repair = provider.requests
    assert repair.messages[:len(first.messages)] == first.messages
    assert "已有触点与线圈资料" not in repair.messages[-1].content
    assert result["approaches"][0]["output_logic"] == contact_output()
    assert result["missing_info"] == []


def test_mcp_repair_response_delivers_schema_and_instruction():
    from application.design_session import DesignSession
    plan = plan_analysis_repair(analysis(False), "X0 控制 Y0。", require_output_logic=True)
    response = DesignSession._repair_request({"repair": plan, "analysis_id": "a", "repair_id": "r"})
    [target] = response["repair_targets"]
    Draft202012Validator(target["value_schema"]).validate(contact_output())
    assert "不把技术方法转交用户" in target["instruction"]
