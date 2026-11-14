"""A malformed real GLM flowchart must not strand an analysis draft."""

from tools.offline_inputs import private_path
import copy
import json
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

import application.model_api as api
from model_runtime.provider import (
    ModelProviderError, ReasoningDelta, ResponseRejectedError,
    TextDelta, UserMessage, response_policy_scope,
)
from application.response_contracts import ANALYSIS_RESPONSE
from application.response_contracts import ANALYSIS_REPAIR_RESPONSE
from design_output_examples import batch_output, latch_output


@pytest.mark.parametrize("section, row, path, replacement", [
    ("requirements", {"id": "R1"}, "/requirements/0/text", "X0 controls Y0"),
    ("implementation_items", {"id": "I1", "language": "ladder", "location": "MAIN", "method": "Latch",
                              "state_owner": "M0", "requirement_ids": "R1"}, "/implementation_items/0/requirement_ids", ["R1"]),
    ("implementation_items", {"id": "I1", "language": "st", "location": "MAIN", "method": "Delay",
                              "state_owner": "MAIN", "instances": [{"delay_ms": 150}]},
     "/implementation_items/0/instances/0", "DelayA"),
    ("open_questions", {"id": "Q1", "question": "What delay is needed?", "required": "yes"},
     "/open_questions/0/required", True),
])
def test_planning_row_shape_is_checked_and_repaired_at_the_failing_path(section, row, path, replacement):
    from jsonschema import Draft202012Validator
    from application.analysis_repair import apply_analysis_repair, design_submission_schema, plan_analysis_repair
    from application.analysis_results import current_analysis_protocol_violations, prepare_analysis_payload

    result = {"summary": "Control plan", "approaches": [], section: [row]}
    plan = plan_analysis_repair(result, "")
    [target] = plan["targets"]
    assert target["path"] == path
    assert not Draft202012Validator(target["value_schema"]).is_valid(target["current_value"])
    section_schema = design_submission_schema()["properties"][section]
    assert not Draft202012Validator(section_schema).is_valid(result[section])
    repaired, _ = apply_analysis_repair(plan, {"repairs": [{"path": path, "value": replacement}]}, "")
    assert current_analysis_protocol_violations(prepare_analysis_payload(repaired)) == []


def test_instruction_choices_use_local_analysis_repair():
    from application.analysis_repair import apply_analysis_repair, plan_analysis_repair

    approach = {"approach_id": "copy", "name": "批量归档", "description": "保留同一批采样",
                "generation_guide": "每批完成后保存记录", "implementation_semantics": [],
                "instruction_choices": [{"opcode": "BMOV"}]}
    payload = {"summary": "按批次保存采样", "approaches": [approach]}
    plan = plan_analysis_repair(payload, "")
    [target] = plan["targets"]
    assert target["path"] == "/approaches/0/instruction_choices"
    choices = [{"opcode": "BMOV", "role": "复制本批数据"}]
    repaired, _ = apply_analysis_repair(plan, {"repairs": [{"path": target["path"], "value": choices}]}, "")
    assert repaired == {**payload, "approaches": [{**approach, "instruction_choices": choices}]}
    assert not plan_analysis_repair(repaired, "")["targets"]


@pytest.mark.parametrize("mode", ["direct", "design"])
def test_decision_basis_repair_preserves_original_turn_and_delivered_facts(monkeypatch, tmp_path, mode):
    from application.conversation_model import ModelTurn, model_turn_scope

    text = "每批采样完成后把连续寄存器归档。"
    knowledge = "批量复制可处理连续字区。"
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *args, **kwargs: knowledge)
    valid = {"variant": "成组复制", "premise": "指令支持连续字区", "status": "known",
             "conclusion": "applicable", "references": [
                 {"source": "model_fact", "ref": "knowledge", "quote": knowledge}]}
    invalid = {"variant": "成组复制", "premise": "每批字数不固定", "status": "known",
               "conclusion": "excluded", "references": [
                   {"source": "request", "ref": "", "quote": "每批字数不固定"}]}
    changes = {"status": "unknown", "conclusion": "conditional", "references": []}
    condition = {**invalid, **changes}
    payload = json.loads(CURRENT_EMPTY)
    approach = payload["approaches"][0]
    approach.update(name="批量归档", generation_guide="每批完成时归档。",
                    output_logic=batch_output(),
                    instruction_choices=[{"opcode": "BMOV", "role": "复制整批数据"}],
                    decision_basis=[valid, invalid])
    patch = {"repairs": [{"path": "/approaches/0/decision_basis/1", "value": changes}]}
    provider = Provider(*(json.dumps(item, ensure_ascii=False) for item in (payload, patch)))
    turn = ModelTurn(tmp_path, (), {"model": "fixture"}, {}, "zh-CN")

    with api.provider_scope(provider), model_turn_scope(turn):
        result = api.analyze_requirement_streaming(text, analysis_mode=mode)

    assert len(provider.requests) == 2
    first, repair = provider.requests
    assert knowledge in first.messages[0].content
    assert repair.response_contract == ANALYSIS_REPAIR_RESPONSE
    assert repair.messages[:len(first.messages)] == first.messages
    assert knowledge not in repair.messages[-1].content
    [target] = json.loads(repair.messages[-1].content)["targets"]
    assert target["path"] == "/approaches/0/decision_basis/1"
    assert target["mode"] == "decision_citation"
    assert target["supported_references"] == []
    saved = result["approaches"][0]
    assert saved["decision_basis"] == [valid, condition]
    assert saved["instruction_choices"] == approach["instruction_choices"]
    assert saved["generation_guide"] == approach["generation_guide"]
    assert knowledge in turn.pending["messages"][0]["content"]
    assert json.loads(turn.pending["messages"][-1]["content"]) == patch
    assert turn.pending["analysis_result"]["approaches"][0]["decision_basis"] == [valid, condition]


def test_named_instance_records_are_preserved_without_another_model_request(monkeypatch):
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *args, **kwargs: "")
    details = {"name": "DelayA", "delay_ms": 150, "outputs": ["Y1"]}
    row = {"id": "I1", "language": "ladder", "location": "MAIN", "method": "分别计时",
           "state_owner": "各通道", "requirement_ids": ["R1"], "instances": [details, "DelayB"]}
    payload = {"summary": "两路输出独立计时。", "approaches": [],
               "requirements": [{"id": "R1", "text": "两路输出独立计时。"}], "implementation_items": [row]}
    provider = Provider(json.dumps(payload, ensure_ascii=False))

    with api.provider_scope(provider):
        result = api.analyze_requirement("两路输出独立计时。")

    assert len(provider.requests) == 1
    [item] = result["implementation_items"]
    assert item["instances"] == ["DelayA", "DelayB"]
    method, instance_details = item["method"].split("\n实例明细 ", 1)
    assert method == row["method"] and json.loads(instance_details) == [details]


def test_plan_choices_are_separate_from_concrete_process_answers():
    from application.analysis_repair import plan_analysis_repair

    approaches = [
        {"approach_id": "queue", "name": "队列", "implementation_semantics": []},
        {"approach_id": "shared", "name": "共享记录", "implementation_semantics": []},
    ]
    payload = {
        "summary": "控制方案",
        "approaches": approaches,
        "missing_info": [
            {"id": "pick", "question": "采用哪种方案？", "required": True,
             "options": ["queue：队列", "shared:共享记录"]},
            {"id": "period", "question": "周期是多少？", "required": True,
             "options": ["填写周期", "提供周期范围，并说明取值依据"]},
            {"id": "fault", "question": "故障时采用哪种输出策略？", "required": True,
             "options": ["保持输出", "立即撤销", "其他，请说明"]},
            {"id": "mode", "question": "选择运行模式。", "required": True,
             "options": ["连续运行", "其他，请说明"]},
            {"id": "custom_mode", "question": "说明自定义运行模式。", "required": True, "options": [],
             "required_when": {"parameter": "mode", "equals": "其他，请说明"}},
        ],
    }
    plan = plan_analysis_repair(payload, "")

    assert not plan["targets"]
    assert plan["base"]["approaches"] == approaches
    assert [(row["id"], row["options"], row["required"]) for row in plan["base"]["missing_info"]] == [
        ("period", [], True), ("fault", ["保持输出", "立即撤销"], True),
        ("mode", ["连续运行", "其他，请说明"], True), ("custom_mode", [], True),
    ]


@pytest.mark.parametrize("stream", [False, True])
def test_unbound_entry_candidates_are_deferred_without_rewriting_usable_confirmation(monkeypatch, stream):
    from test_spec_choice_metadata import ENTRY_FIXTURE
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *args, **kwargs: "")
    raw = json.dumps(ENTRY_FIXTURE["analysis"], ensure_ascii=False)
    provider = Provider(raw)
    repairs = []
    function = api.analyze_requirement_streaming if stream else api.analyze_requirement
    with api.provider_scope(provider):
        result = function(ENTRY_FIXTURE["request"], on_format_repair=lambda: repairs.append(True))
    assert len(provider.requests) == 1 and repairs == []
    assert provider.responses == []
    assert result["execution_semantics"] == []
    assert len(result["execution_intent_receipt"]["pending"]) == 2
    assert result["execution_intent_claims"] == []
    assert result["missing_info"] == ENTRY_FIXTURE["analysis"]["missing_info"]
    assert not result["suggested_io"]
    assert "上升沿" not in result["summary"] and "重新按" not in result["summary"]


@pytest.mark.parametrize("damage", ["evidence", "states", "shape"])
def test_unbound_candidate_deferral_does_not_hide_other_protocol_errors(damage):
    from test_spec_choice_metadata import ENTRY_FIXTURE
    from application.analysis_results import prepare_analysis_payload, current_analysis_protocol_violations
    result = copy.deepcopy(ENTRY_FIXTURE["analysis"])
    result["execution_intent_claims"] = result["execution_intent_claims"][:1]
    claim = result["execution_intent_claims"][0]
    if damage == "evidence":
        claim["evidence"] = ["fabricated evidence"]
    elif damage == "states":
        claim["trigger"].pop("from")
    else:
        claim["trigger"]["source_devices"] = "X0"
    prepared = prepare_analysis_payload(result, contract_stage="requirements", user_text=ENTRY_FIXTURE["request"])
    assert current_analysis_protocol_violations(prepared)
    assert not prepared.get("_deferred_execution_claims")


def test_explicit_bound_rising_requirement_is_preserved_and_never_deferred(monkeypatch):
    from test_spec_choice_metadata import ENTRY_FIXTURE
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *args, **kwargs: "")
    result = copy.deepcopy(ENTRY_FIXTURE["analysis"])
    text = "X1 上升沿启动 Y0"
    result["execution_intent_claims"] = [{"trigger": {"kind": "transition", "source_devices": ["X1"], "from": False, "to": True}, "evidence": [text]}]
    provider = Provider(json.dumps(result, ensure_ascii=False))
    with api.provider_scope(provider):
        normalized = api.analyze_requirement(text)
    assert len(provider.requests) == 1
    assert any(r["semantic"] == "RISING_EDGE" for r in normalized["execution_semantics"])
    assert normalized["execution_intent_receipt"]["pending"] == []


# Minimized from the 2026-09-10 GLM stream, which ended with finish_reason=stop
# even though the transition object was missing its label key.
BROKEN = '{"summary":"起保停控制","approaches":[],"flowchart_steps":[{"type":"transition":"X0启动"}]}'
FIXED = '{"summary":"起保停控制","approaches":[],"flowchart_steps":[{"type":"transition","label":"X0启动"}]}'
LEGACY_PROTOCOL = '{"summary":"起保停控制","approaches":[{"name":"旧协议","generation_contract":{"required_structures":["self_hold"]}}]}'
CURRENT_EMPTY = '{"summary":"起保停控制","approaches":[{"name":"当前协议","implementation_semantics":[],"decision_basis":[]}],"execution_intent_claims":[],"explicit_constraint_claims":[]}'
MIXED_PROTOCOL = '{"summary":"起保停控制","approaches":[{"name":"新","implementation_semantics":[]},{"name":"旧","generation_contract":{"required_structures":["self_hold"]}}]}'
LOW_LEVEL_SEMANTIC = '{"summary":"起保停控制","approaches":[{"name":"错误","implementation_semantics":[{"kind":"opcode","status":"required","value":"SFTL"}]}]}'


class Provider:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        yield ReasoningDelta("检查回复格式。")
        yield TextDelta(response)


def test_design_repairs_missing_approaches_and_requirement_links_together(monkeypatch):
    from jsonschema import Draft202012Validator

    monkeypatch.setattr(api, "_build_knowledge_context", lambda *args, **kwargs: "")
    item = {"id": "main", "language": "ladder", "location": "MAIN", "method": "风机启停自保持",
            "state_owner": "M0", "requirement_ids": ["start"]}
    initial = {"requirements": [{"id": "start", "text": "按下启动后保持运行。"},
                                {"id": "stop", "text": "按下停止后关闭风机。"}],
               "implementation_items": [item], "open_questions": []}
    approach = {"approach_id": "latch", "name": "自保持", "description": "用运行允许位控制风机。", "decision_basis": [],
                "output_logic": latch_output(),
                "implementation_semantics": [{"kind": "structure", "status": "required", "value": "self_hold"}]}
    patch = {"repairs": [
        {"path": "/approaches", "value": [approach]},
        {"path": "/summary", "value": "风机启停控制。"},
        {"path": "/implementation_items", "value": {"main": ["stop"]}},
    ]}
    provider = Provider(json.dumps(initial, ensure_ascii=False), json.dumps(patch, ensure_ascii=False))
    context = {"plc_model": "FX3U", "target_mode": "ladder"}
    with api.provider_scope(provider):
        result = api.analyze_requirement_streaming("生成风机启停程序。", analysis_mode="design",
                                                   planning_context={**context, "working_draft": context})

    assert len(provider.requests) == 2
    assert "including summary and approaches" in provider.requests[0].messages[0].content
    targets = {t["path"]: t for t in json.loads(provider.requests[1].messages[-1].content)["targets"]}
    assert set(targets) == {"/approaches", "/summary", "/implementation_items"}
    validator = Draft202012Validator(targets["/approaches"]["value_schema"])
    assert validator.is_valid([approach])
    assert not validator.is_valid([{**approach, "implementation_semantics": "生成风机启停程序。"}])
    assert result["summary"] == "风机启停控制。"
    assert result["approaches"][0]["approach_id"] == "latch"
    assert result["requirements"] == initial["requirements"]
    assert result["implementation_items"] == [{**item, "requirement_ids": ["start", "stop"]}]


@pytest.mark.parametrize(("turn", "links", "saved"), [
    ("initial", {"main-network": ["indicator", "stop"]}, True),
    ("followup", {"main-network": ["indicator", "stop"]}, True),
    ("followup", {"main-network": ["indicator"]}, False),
    ("followup", {"unknown-network": ["indicator", "stop"]}, False),
    ("complete", None, True),
])
def test_design_coverage_repair_runs_before_draft_is_saved(monkeypatch, tmp_path, turn, links, saved):
    from application.workbench import WorkbenchService

    monkeypatch.setattr(api, "_build_knowledge_context", lambda *args, **kwargs: "")
    enable = {"id": "enable", "text": "启动后保持运行。"}
    missing = [{"id": "indicator", "text": "运行时点亮指示灯。"},
               {"id": "stop", "text": "停止时撤销运行输出。"}]
    item = {"id": "main-network", "language": "ladder", "location": "MAIN", "method": "启停及运行指示",
            "state_owner": "M0", "requirement_ids": ["enable"]}
    analysis = json.loads(CURRENT_EMPTY)
    analysis["approaches"][0]["output_logic"] = latch_output()
    analysis["requirements"] = missing if links is not None else [enable]
    if turn == "initial":
        analysis["requirements"] = [enable, *missing]
        analysis["implementation_items"] = [item]
    responses = [json.dumps(analysis, ensure_ascii=False)]
    if links is not None:
        responses.append(json.dumps({"repairs": [{"path": "/implementation_items", "value": links}]}))
    provider = Provider(*responses)
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state",
                               model_factory=lambda: (provider, {"model": "unit-fake"})).start()
    try:
        project_id = service.create_project(name="Coverage", plc_model="FX3U")["id"]
        commands = service.project_commands
        before = commands.get_spec_document(project_id)
        if turn != "initial":
            before = commands.update_spec_document(project_id, [
                {"section": "requirements", "id": enable["id"], "value": enable},
                {"section": "implementation_items", "id": item["id"], "value": item},
            ], before["draft_revision"])
        job = service.submit({"kind": "analysis", "project_id": project_id, "request_id": "coverage",
                              "text": "补充控制需求。", "analysis_mode": "design", "response_language": "zh-CN",
                              "start_conversation": True})
        finished = service.jobs.wait(job["id"], timeout=10)
        after = commands.get_spec_document(project_id)
        assert len(provider.requests) == len(responses), finished
        if links is not None:
            request = provider.requests[1]
            assert request.response_contract == ANALYSIS_REPAIR_RESPONSE
            [target] = json.loads(request.messages[-1].content)["targets"]
            assert target["missing_requirements"] == missing
            assert target["current_value"] == [item]
        if saved:
            assert finished["status"] == "completed", finished
            expected_refs = ["enable", "indicator", "stop"] if links is not None else ["enable"]
            assert after["draft"]["implementation_items"] == [{**item, "requirement_ids": expected_refs}]
            assert after["draft"]["requirements"] == ([enable, *missing] if links is not None else [enable])
            assert not [issue for issue in after["issues"]["errors"] if issue["code"] == "invalid_project_plan"]
        else:
            assert finished["status"] == "failed", finished
            assert after["draft_revision"] == before["draft_revision"]
    finally:
        service.close()


@pytest.mark.parametrize("content", [BROKEN, LOW_LEVEL_SEMANTIC])
def test_primary_comparison_disables_syntax_and_claim_repair(content):
    from application.analysis_results import AnalysisProtocolError
    provider = Provider(content, FIXED)
    published = []
    with api.provider_scope(provider), pytest.raises((ResponseRejectedError, AnalysisProtocolError)):
        api._request_analysis_response([UserMessage("起保停")], allow_repair=False, max_retries=0,
                                       on_content_chunk=published.append)
    assert len(provider.requests) == 1
    assert provider.requests[0].max_retries == 0
    assert provider.responses == [FIXED]
    assert not published


@pytest.mark.parametrize("stream", [False, True])
def test_real_analysis_path_corrects_syntax_once_before_publishing(stream, monkeypatch, tmp_path):
    from dataclasses import replace
    from application.conversation_model import ModelTurn, model_turn_scope
    from model_runtime.provider import with_response_language

    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **kw: "")
    provider = Provider(BROKEN, FIXED)
    events = []
    callback = lambda: events.append("repair")
    options = {"on_format_repair": callback, "response_language": "zh-CN"}
    if stream:
        options["on_content_chunk"] = lambda text: events.append(text)
    function = api.analyze_requirement_streaming if stream else api.analyze_requirement
    model = {"model": "captured-profile"}
    turn = ModelTurn(tmp_path, (), model, {}, "zh-CN")
    with api.provider_scope(provider, model_name="captured-profile"), model_turn_scope(turn):
        result = function("起保停", **options)
    assert result["summary"] == "起保停控制"
    assert "flowchart_steps" not in result  # Old replies remain parseable, not new model work.
    assert len(provider.requests) == 2
    first, second = provider.requests
    assert first.stream is second.stream is stream
    assert first.response_contract == second.response_contract == ANALYSIS_RESPONSE
    assert first.response_language == second.response_language == "zh-CN"
    assert first.options == second.options
    assert second.messages[:len(first.messages)] == first.messages
    assert second.messages[-2].content == BROKEN
    assert "rejected_draft" not in json.loads(second.messages[-1].content)
    assert "line 1, column" in second.messages[-1].content
    assert not first.tools and not second.tools
    assert events == (["repair", FIXED] if stream else ["repair"])
    turn.save("job_analysis")
    continued = ModelTurn(tmp_path, ("job_analysis",), model, {}, "zh-CN")
    restored = with_response_language(replace(first, messages=continued.history[:-1]))
    # The fixture has display reasoning only. Real provider replay fields
    # are persisted independently of the UI reasoning string.
    expected = (*second.messages[:-2], replace(second.messages[-2], reasoning=""), second.messages[-1])
    assert restored.messages == expected
    assert json.loads(continued.history[-1].content)["summary"] == result["summary"]


def test_corrected_analysis_keeps_both_attempts_private_and_history_unchanged():
    provider = Provider(BROKEN, FIXED)
    messages = [{"role": "user", "content": "起保停"}]
    original = copy.deepcopy(messages)
    with api.provider_scope(provider):
        result = api._request_analysis_response(messages, stream=True)
    assert messages == original
    assert [a.message.content for a in result.raw_attempts] == [BROKEN, FIXED]
    assert result.message.content == FIXED
    assert BROKEN not in repr(result)


@pytest.mark.parametrize("content", [FIXED, "```json\n" + FIXED + "\n```"])
def test_valid_analysis_uses_one_request(content):
    provider = Provider(content)
    with api.provider_scope(provider):
        result = api._request_analysis_response([UserMessage("起保停")])
    assert result.message.content == content
    assert len(provider.requests) == 1


@pytest.mark.parametrize("stream", [False, True])
def test_bound_source_without_its_own_evidence_stays_pending_without_model_repair(stream):
    text = "X001 启动，X003 停止，M0 运行保持。系统运行时 Y000、Y002 保持运行。当 M0 = ON 时允许移位。"
    draft = json.loads(CURRENT_EMPTY)
    draft["missing_info"] = [{"id": "polarity", "question": "启动和停止按下时的有效电平？", "required": True}]
    draft["execution_intent_claims"] = [{"trigger": {"kind": "level", "source_devices": ["M0"]},
                                        "evidence": ["系统运行时 Y000、Y002 保持运行。"]}]
    replacement = [{"trigger": {"kind": "level", "source_devices": ["M0"]},
                    "evidence": ["当 M0 = ON 时允许移位。"]}]
    provider = Provider(json.dumps(draft, ensure_ascii=False), json.dumps({"repairs": [
        {"path": "/execution_intent_claims/0", "value": replacement}]}, ensure_ascii=False))
    with api.provider_scope(provider):
        response = api._request_analysis_response([UserMessage(text)], stream=stream, user_text=text)
    assert len(provider.requests) == 1 and len(provider.responses) == 1
    normalized = api._parse_analysis_response(response.message.content, user_text=text)
    assert normalized["execution_intent_claims"] == []
    assert normalized["execution_intent_receipt"]["accepted"] == []
    assert normalized["execution_intent_receipt"]["pending"] == [{
        "index": 0, "status": "pending_binding", "reason": "claimed_device_not_in_evidence",
        "candidate": draft["execution_intent_claims"][0]}]
    assert normalized["missing_info"] == draft["missing_info"]


@pytest.mark.parametrize("case", json.loads((private_path('tests/fixtures/analysis_bound_evidence.json')).read_text(encoding="utf-8"))["cases"],
                         ids=lambda case: case["id"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("allow_repair", [False, True])
def test_captured_complete_analysis_preserves_unbound_claim_as_inactive(case, stream, allow_repair):
    from plc.execution_intent import compile_execution_intent_claims
    from plc.specification.confirmed import build_review_draft, canonicalize_confirmed_spec
    raw = json.dumps(case["response"], ensure_ascii=False)
    original = copy.deepcopy(case)
    claims = case["response"]["execution_intent_claims"]
    # The executable grounding rule itself remains strict.
    core = compile_execution_intent_claims(claims, case["user_text"])
    assert core["rejected"] == [{"index": 1, "reason": "claimed_device_not_in_evidence"}]
    provider = Provider(raw)
    with api.provider_scope(provider):
        response = api._request_analysis_response([UserMessage(case["user_text"])], stream=stream,
            user_text=case["user_text"], allow_repair=allow_repair, max_retries=0)
    result = api._parse_analysis_response(response.message.content, user_text=case["user_text"])
    assert len(provider.requests) == 1 and response.raw_attempts[0].message.content == raw
    assert result["execution_intent_claims"] == claims[:1]
    assert result["execution_intent_receipt"]["accepted"] == core["accepted"]
    assert result["execution_intent_receipt"]["pending"] == [{
        "index": 1, "status": "pending_binding", "reason": "claimed_device_not_in_evidence", "candidate": claims[1]}]
    assert result["missing_info"] == case["response"]["missing_info"]
    confirmed = canonicalize_confirmed_spec(build_review_draft(result))
    assert confirmed["execution_semantics"] == result["execution_semantics"]
    forbidden = "FALLING_EDGE" if claims[1]["trigger"]["kind"] == "transition" else "LEVEL"
    device = claims[1]["trigger"]["source_devices"][0]
    assert not any(row["semantic"] == forbidden and device in row["devices"]
                   and row.get("source") == "agent_a_claim" for row in confirmed["execution_semantics"])
    assert case == original


@pytest.mark.parametrize("damage", ["fabricated_evidence", "illegal_address", "malformed_transition", "explicit_constraint"])
def test_pending_binding_does_not_mask_other_claim_failures(damage):
    from application.analysis_results import AnalysisProtocolError, prepare_analysis_payload
    draft = json.loads(CURRENT_EMPTY)
    text = "X0：启动按钮。按启动后运行。必须使用 SEGD。"
    claim = {"trigger": {"kind": "level", "source_devices": ["X0"]}, "evidence": ["按启动后运行。"]}
    draft["execution_intent_claims"] = [claim]
    if damage == "fabricated_evidence":
        claim["evidence"] = ["现场确认已验收"]
    elif damage == "illegal_address":
        claim["trigger"]["source_devices"] = ["NOT_A_DEVICE"]
    elif damage == "malformed_transition":
        claim["trigger"] = {"kind": "transition", "source_devices": ["X0"], "to": 1}
    else:
        draft["explicit_constraint_claims"] = [{"operation": "require", "scope": "global",
            "target": {"kind": "opcode", "values": ["MOV"]}, "evidence": ["必须使用 SEGD。"]}]
    prepared = prepare_analysis_payload(draft, user_text=text)
    assert bool(prepared.get("_deferred_execution_claims")) == (damage == "explicit_constraint")
    with pytest.raises(AnalysisProtocolError):
        api._validate_fresh_analysis_content(json.dumps(draft, ensure_ascii=False), user_text=text)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_confirmed_unique_alias_stays_active_and_ambiguous_alias_stays_pending(ambiguous):
    text = "按停止按钮时复位输出。"
    spec = {"io_table": [{"kind": "X", "address": "X2", "label": "停止按钮"}]}
    if ambiguous:
        spec["io_table"].append({"kind": "X", "address": "X3", "label": "停止按钮"})
    draft = json.loads(CURRENT_EMPTY)
    draft["execution_intent_claims"] = [{"trigger": {"kind": "level", "source_devices": ["X2"]}, "evidence": [text]}]
    result = api._parse_analysis_response(json.dumps(draft, ensure_ascii=False), user_text=text, confirmed_spec=spec)
    assert bool(result["execution_intent_receipt"]["pending"]) is ambiguous
    assert bool(result["execution_intent_receipt"]["accepted"]) is not ambiguous


def test_pending_claim_keeps_original_repair_indices_and_cannot_be_patched_as_validated():
    from application.analysis_repair import plan_analysis_repair, apply_analysis_repair
    from application.analysis_results import AnalysisProtocolError
    text = "X0：启动按钮。按启动后运行。X1从0变为1时触发。"
    pending = {"trigger": {"kind": "level", "source_devices": ["X0"]}, "evidence": ["按启动后运行。"]}
    broken = {"trigger": {"kind": "transition", "source_devices": ["X1"], "to": 1}, "evidence": ["X1从0变为1时触发。"]}
    draft = json.loads(CURRENT_EMPTY)
    draft["execution_intent_claims"] = [pending, broken]
    plan = plan_analysis_repair(draft, text)
    assert [target["path"] for target in plan["targets"]] == ["/execution_intent_claims/1"]
    with pytest.raises(AnalysisProtocolError, match="unlisted"):
        apply_analysis_repair(plan, {"repairs": [{"path": "/execution_intent_claims/0", "value": []}]}, text)
    fixed = copy.deepcopy(broken)
    fixed["trigger"]["from"] = 0
    payload, _ = apply_analysis_repair(plan, {"repairs": [{"path": "/execution_intent_claims/1", "value": [fixed]}]}, text)
    result = api._parse_analysis_response(json.dumps(payload, ensure_ascii=False), user_text=text)
    assert result["execution_intent_receipt"]["pending"][0]["candidate"] == pending
    assert result["execution_intent_claims"] == [fixed]


@pytest.mark.parametrize("text,trigger", [
    ("首扫执行初始化。", {"kind": "first_scan", "source_devices": []}),
    ("每隔100ms执行一次。", {"kind": "cyclic", "source_devices": [], "period_ms": 100}),
])
def test_empty_scan_evidence_can_be_reextracted_when_the_current_request_is_explicit(text, trigger):
    from application.analysis_repair import apply_analysis_repair, plan_analysis_repair
    from application.analysis_results import AnalysisProtocolError
    draft = json.loads(CURRENT_EMPTY)
    draft["execution_intent_claims"] = [{"trigger": trigger, "evidence": []}]
    plan = plan_analysis_repair(draft, text)
    path = "/execution_intent_claims/0"
    assert not plan["targets"][0]["must_preserve"].get("unsupported_candidate")
    with pytest.raises(AnalysisProtocolError, match="cannot be removed"):
        apply_analysis_repair(plan, {"repairs": [{"path": path, "value": []}]}, text)
    for invalid_evidence in (None, 123, "首扫", ["PLC："]):
        with pytest.raises(AnalysisProtocolError, match="explicit current-request evidence"):
            apply_analysis_repair(plan, {"repairs": [{"path": path, "value": [
                {"trigger": trigger, "evidence": invalid_evidence}]}]}, text)
    replacement = {"trigger": trigger, "evidence": [text]}
    repaired, _ = apply_analysis_repair(plan, {"repairs": [{"path": path, "value": [replacement]}]}, text)
    api._validate_fresh_analysis_content(json.dumps(repaired), user_text=text)
    assert repaired["execution_intent_claims"] == [replacement]


@pytest.mark.parametrize("bad", [
    "", "[]", "not json", '{"summary":{"unexpected":"value"}}',
])
def test_other_acceptance_failures_do_not_trigger_format_correction(bad):
    provider = Provider(bad)
    with api.provider_scope(provider), pytest.raises(ResponseRejectedError):
        api._request_analysis_response([UserMessage("起保停")])
    assert len(provider.requests) == 1



@pytest.mark.parametrize("first", [LEGACY_PROTOCOL, MIXED_PROTOCOL, LOW_LEVEL_SEMANTIC])
def test_fresh_agent_a_protocol_violation_uses_one_existing_format_repair(first):
    index = 1 if first == MIXED_PROTOCOL else 0
    patches = ([{"path": f"/approaches/{index}/generation_contract", "value": None},
                {"path": f"/approaches/{index}/implementation_semantics", "value": []}]
               if first != LOW_LEVEL_SEMANTIC else [{"path": "/approaches/0/implementation_semantics/0", "value": None}])
    provider = Provider(first, json.dumps({"repairs": patches}, ensure_ascii=False))
    events = []
    with api.provider_scope(provider):
        result = api._request_analysis_response(
            [UserMessage("起保停")],
            on_format_repair=lambda: events.append("repair"),
        )
    from application.analysis_results import current_analysis_protocol_violations
    assert current_analysis_protocol_violations(json.loads(result.message.content)) == []
    assert json.loads(result.message.content)["approaches"][0]["name"] == json.loads(first)["approaches"][0]["name"]
    assert len(provider.requests) == 2
    assert events == ["repair"]
    correction = provider.requests[1].messages[-1].content
    assert "implementation_semantics" in correction
    assert len(provider.requests[1].messages) == 2
    assert provider.requests[1].response_contract == ANALYSIS_REPAIR_RESPONSE
    assert all(t["path"].startswith("/approaches/") for t in json.loads(correction)["targets"])


def test_execution_claim_metadata_does_not_force_format_repair():
    content = json.dumps({
        "summary": "复杂事件",
        "approaches": [{"name": "当前协议", "implementation_semantics": []}],
        "explicit_constraint_claims": [{
            "operation": "require",
            "scope": "global",
            "target": {"kind": "opcode", "values": ["MOV"]},
            "evidence": ["固定使用 MOV。"],
        }],
        "execution_intent_claims": [
            {
                "trigger": {
                    "kind": "transition",
                    "source_devices": ["X2"],
                    "from": 0,
                    "to": 1,
                },
                "effect": {"kind": "pulse", "devices": ["M1"]},
                "rearm": True,
                "evidence": ["X2 每次从 0 变成 1 时，M1 只产生一个扫描周期事件。"],
            },
            {
                "trigger": {"kind": "level", "source_devices": ["X1"]},
                "effect": {"kind": "reset", "devices": ["M0"]},
                "rearm": True,
                "evidence": ["X1 动作时必须立即解除 M0。"],
            },
            {
                "trigger": {
                    "kind": "transition",
                    "source_devices": ["M1"],
                    "from": 0,
                    "to": 1,
                },
                "effect": {"kind": "count", "devices": ["C0"]},
                "rearm": True,
                "evidence": ["M1 从 0 变成 1 时，对 C0 计数一次；"],
            },
        ],
        "missing_info": [],
        "suggested_io": {},
        "hardware_config": {},
        "assumptions": [],
    }, ensure_ascii=False)
    provider = Provider(content)
    with api.provider_scope(provider):
        text = "固定使用 MOV。" + "".join(span for c in json.loads(content)["execution_intent_claims"] for span in c["evidence"])
        result = api._request_analysis_response([UserMessage(text)])
    assert result.message.content == content
    assert len(provider.requests) == 1


def test_empty_implementation_semantics_is_valid_current_protocol():
    provider = Provider(CURRENT_EMPTY)
    with api.provider_scope(provider):
        result = api._request_analysis_response([UserMessage("起保停")])
    assert result.message.content == CURRENT_EMPTY
    assert len(provider.requests) == 1


def test_second_current_protocol_failure_is_protocol_error_not_plc_error():
    from application.analysis_results import AnalysisProtocolError
    from plc.validation import PLCJsonValidationError

    invalid_patch = json.dumps({"repairs": [
        {"path": "/approaches/0/generation_contract", "value": None},
        {"path": "/approaches/0/implementation_semantics", "value": {}},
    ]})
    provider = Provider(LEGACY_PROTOCOL, invalid_patch)
    with api.provider_scope(provider), pytest.raises(AnalysisProtocolError) as caught:
        api._request_analysis_response([UserMessage("起保停")])
    assert not isinstance(caught.value, PLCJsonValidationError)
    assert len(provider.requests) == 2
    assert len(caught.value.raw_attempts) == 2
    assert any("implementation_semantics" in item for item in caught.value.violations)


def test_parse_analysis_response_never_auto_detects_fresh_legacy_protocol():
    from application.analysis_results import AnalysisProtocolError

    with pytest.raises(AnalysisProtocolError):
        api._parse_analysis_response(LEGACY_PROTOCOL, "FX3U", "起保停")


def test_transport_failure_is_not_a_format_correction_signal():
    provider = Provider(ModelProviderError("unavailable", code="unavailable"))
    with api.provider_scope(provider), pytest.raises(ModelProviderError):
        api._request_analysis_response([UserMessage("起保停")])
    assert len(provider.requests) == 1


@pytest.mark.parametrize("second", [BROKEN, '{"summary":"Start the motor."}'])
def test_failed_correction_remains_rejected_without_publishing_or_third_request(second):
    provider = Provider(BROKEN, second)
    content = []
    reasoning = []
    with api.provider_scope(provider), pytest.raises(ResponseRejectedError) as caught:
        api._request_analysis_response([UserMessage("起保停")], stream=True,
                                       on_content_chunk=content.append,
                                       on_reasoning_chunk=reasoning.append)
    assert len(provider.requests) == 2
    assert [a.message.content for a in caught.value.raw_attempts] == [BROKEN, second]
    assert caught.value.raw_response.message.content == second
    assert content == reasoning == []


def test_analysis_prompt_contains_valid_json_examples():
    example = api.ANALYSIS_SYSTEM_PROMPT.split("返回纯JSON（不要```json包裹），格式：\n", 1)[1]
    example = example.split("\n# suggested_io", 1)[0]
    assert isinstance(json.loads(example), dict)
    assert set(json.loads(example)) == {
        "summary", "approaches", "execution_intent_claims", "explicit_constraint_claims", "missing_info", "suggested_io", "hardware_config", "assumptions",
    }


SFTL_FIXTURE = json.loads((private_path('tests/fixtures/analysis_sftl_repair.json')).read_text(encoding="utf-8"))


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("responses", [False, True])
def test_real_sftl_draft_repairs_claims_and_freezes_every_other_field(stream, responses):
    from application.analysis_results import _normalize_analysis_result
    from application.analysis_repair import plan_analysis_repair
    from model_runtime.provider import _ProviderState, _chat_message, _response_input
    from responses_fixture import response_body

    text = SFTL_FIXTURE["request"]
    raw = SFTL_FIXTURE["first_response"]
    patch = json.dumps(SFTL_FIXTURE["repair_response"], ensure_ascii=False)
    origin = ("offline", "captured-profile", "test-scope")
    class ReplayProvider(Provider):
        def stream(self, request):
            stage = request.response_contract.name
            replay = {"responses_items": response_body(self.responses[0], encrypted=stage)["output"]} if responses else {
                "message": {"reasoning_content": stage}}
            yield _ProviderState({"origin": origin, **replay})
            yield from super().stream(request)
    provider = ReplayProvider(raw, patch)
    content, reasoning, repairs = [], [], []
    messages = [{"role": "system", "content": "FULL_MANUAL_SENTINEL " * 3000}, UserMessage(text)]
    before = copy.deepcopy(messages)
    with api.provider_scope(provider), response_policy_scope(enforce_language=False):
        response = api._request_analysis_response(messages, user_text=text, stream=stream,
            on_content_chunk=content.append, on_reasoning_chunk=reasoning.append,
            on_format_repair=lambda: repairs.append(True))
    assert messages == before and len(provider.requests) == 2 and repairs == [True]
    assert provider.requests[1].response_contract == ANALYSIS_REPAIR_RESPONSE
    assert "FULL_MANUAL_SENTINEL" not in repr(provider.requests[1].messages)
    assert sum(len(m.content) for m in provider.requests[1].messages) < 8000
    assert content == [response.message.content] and patch not in content
    assert [a.message.content for a in response.raw_attempts] == [raw, patch]
    assistant = {"role": "assistant", "content": response.message.content}
    if responses:
        assert not response.message._provider_fields
        assert _response_input(response.message, origin) == [assistant]
    else:
        assert _chat_message(response.message, origin) == {**assistant, "reasoning_content": "analysis"}
    base, result = json.loads(raw), json.loads(response.message.content)
    assert {k: v for k, v in result.items() if k not in {"execution_intent_claims", "explicit_constraint_claims"}} == {
        k: v for k, v in base.items() if k not in {"execution_intent_claims", "explicit_constraint_claims"}}
    assert result["execution_intent_claims"] == base["execution_intent_claims"][1:]
    normalized = _normalize_analysis_result(result, "FX3U", text)
    contract = normalized["approaches"][0]["generation_contract"]
    assert contract["instruction_instances"] == [
        {"opcode": "SFTL", "operands": ["M10", "M100", "K128", "K1"]},
        {"opcode": "SFTL", "operands": ["M11", "M300", "K128", "K1"]},
        {"opcode": "SFTL", "operands": ["M12", "M500", "K128", "K1"]},
    ]
    assert set(contract["forbidden_devices"]) == {"M8011", "M8012"}
    assert {"Y0", "Y2", "Y4", "Y6", "Y10"} <= set(contract["required_devices"])
    assert normalized["explicit_constraint_receipt"]["rejected"] == []
    assert normalized["execution_intent_receipt"]["rejected"] == []
    assert not any(c["semantic"] == "FIRST_SCAN" for c in normalized["execution_semantics"])
    assert any("手工逐位移位" in "".join(c["evidence"]) for c in response.repair_receipt["retained_text"])
    plan = plan_analysis_repair(base, text)
    assert set(c["path"] for c in plan["targets"]) == set(p["path"] for p in SFTL_FIXTURE["repair_response"]["repairs"])


@pytest.mark.parametrize("damage", ["extra_path", "duplicate", "missing", "changed_instance", "changed_scope", "fake_evidence", "drop_target", "invent_scan", "full_object"])
def test_local_repair_rejects_untrusted_changes_without_leaking_or_third_call(damage):
    from application.analysis_results import AnalysisProtocolError
    patch = copy.deepcopy(SFTL_FIXTURE["repair_response"])
    rows = patch["repairs"]
    if damage == "extra_path":
        rows.append({"path": "/missing_info", "value": []})
    elif damage == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif damage == "missing":
        rows.pop()
    elif damage == "full_object":
        patch = json.loads(SFTL_FIXTURE["first_response"])
    elif damage == "invent_scan":
        rows[0]["value"] = [{"trigger": {"kind": "first_scan", "source_devices": []}, "evidence": ["PLC："]}]
    elif damage == "drop_target":
        rows[-1]["value"] = []
    else:
        claim = rows[-1]["value"][0]
        if damage == "changed_instance":
            claim["target"]["operands"][2] = "K64"
        elif damage == "changed_scope":
            claim["scope"] = "scoped"
        else:
            claim["evidence"] = ["invented evidence"]
    raw_patch = json.dumps(patch, ensure_ascii=False)
    provider = Provider(SFTL_FIXTURE["first_response"], raw_patch)
    content, reasoning = [], []
    with api.provider_scope(provider), response_policy_scope(enforce_language=False), pytest.raises(AnalysisProtocolError) as rejected:
        api._request_analysis_response([UserMessage(SFTL_FIXTURE["request"])], stream=True,
            on_content_chunk=content.append, on_reasoning_chunk=reasoning.append)
    assert len(provider.requests) == 2 and content == reasoning == []
    assert [a.message.content for a in rejected.value.raw_attempts] == [SFTL_FIXTURE["first_response"], raw_patch]


def test_repair_metadata_cannot_be_forged_by_model(monkeypatch):
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **kw: "")
    payload = json.loads(CURRENT_EMPTY)
    payload["analysis_repair_receipt"] = {"model_paths": ["forged"]}
    provider = Provider(json.dumps(payload, ensure_ascii=False))
    with api.provider_scope(provider):
        result = api.analyze_requirement("起保停")
    assert "analysis_repair_receipt" not in result


def test_failure_inventory_does_not_stop_at_a_broken_approaches_container():
    from application.analysis_repair import plan_analysis_repair
    draft = {"approaches": {}, "execution_intent_claims": [{"trigger": {"kind": "first_scan"}, "evidence": []}],
             "explicit_constraint_claims": [{"operation": "require", "scope": "global", "target": {"kind": "unknown"}, "evidence": []}]}
    plan = plan_analysis_repair(draft, "只分析，不添加新硬件。")
    assert {t["path"] for t in plan["targets"]} == {"/approaches", "/execution_intent_claims/0", "/explicit_constraint_claims/0"}


@given(container=st.sampled_from(["category", "kind"]), alias=st.sampled_from(["opcodes", "devices", "instruction_instances", "instruction_instance"]),
       count=st.integers(1, 3), note=st.text(max_size=35))
def test_lossless_representation_normalization_is_idempotent(container, alias, count, note):
    from plc.specification.explicit_constraint_claims import normalize_explicit_claim_representations
    instances = [
        {"opcode": "SFTL", "operands": ["M10", "M100", "K128", "K1"]},
        {"opcode": "SFTL", "operands": ["M11", "M300", "K128", "K1"]},
        {"opcode": "SFTL", "operands": ["M12", "M500", "K128", "K1"]},
    ][:count]
    values = instances if alias.startswith("instruction_instance") else ["SFTL"] if alias == "opcodes" else ["Y000"]
    original = [{"operation": "require", "scope": "global", "evidence": ["原文"], "note": note,
                 "target": {container: alias, "values": values}}]
    before = copy.deepcopy(original)
    normalized, changes = normalize_explicit_claim_representations(original)
    expected_targets = [{"kind": "instruction_instance", **i} for i in instances] if alias.startswith("instruction_instance") else [
        {"kind": "opcode" if alias == "opcodes" else "device", "values": values}]
    assert normalized == [{**original[0], "target": t} for t in expected_targets]
    assert original == before and changes
    repeated, second_changes = normalize_explicit_claim_representations(normalized)
    assert repeated == normalized and second_changes == []


@given(order=st.permutations(range(6)), summary=st.text(max_size=50), question=st.text(max_size=50),
       metadata=st.dictionaries(st.text(min_size=1, max_size=8), st.integers(), max_size=5))
def test_patch_splices_freeze_nonfailed_fields_and_never_lose_specified_targets(order, summary, question, metadata):
    from application.analysis_repair import plan_analysis_repair, apply_analysis_repair
    from plc.specification.explicit_constraint_claims import compile_explicit_constraint_claims
    raw = json.loads(SFTL_FIXTURE["first_response"])
    raw["summary"] = summary
    raw["missing_info"] = [{"question": question, "required": True, "options": ["待确认"]}]
    raw["opaque_metadata"] = metadata
    before = copy.deepcopy(raw)
    plan = plan_analysis_repair(raw, SFTL_FIXTURE["request"])
    patch = {"repairs": [copy.deepcopy(SFTL_FIXTURE["repair_response"]["repairs"][i]) for i in order]}
    merged, receipt = apply_analysis_repair(plan, patch, SFTL_FIXTURE["request"])
    for key in raw.keys() - {"explicit_constraint_claims", "execution_intent_claims"}:
        assert merged[key] == before[key]
    assert raw == before
    compiled = compile_explicit_constraint_claims(merged["explicit_constraint_claims"], SFTL_FIXTURE["request"])
    exact = [op["instance"] for op in compiled["operations"] if op["kind"] == "instruction_instance"]
    assert exact == [
        {"opcode": "SFTL", "operands": ["M10", "M100", "K128", "K1"]},
        {"opcode": "SFTL", "operands": ["M11", "M300", "K128", "K1"]},
        {"opcode": "SFTL", "operands": ["M12", "M500", "K128", "K1"]},
    ]
    assert compiled["rejected"] == [] and len(receipt["model_paths"]) == 6


def test_internal_patch_does_not_reach_provisional_preview():
    raw_patch = json.dumps(SFTL_FIXTURE["repair_response"], ensure_ascii=False)
    provider, previews = Provider(SFTL_FIXTURE["first_response"], raw_patch), []
    with api.provider_scope(provider), response_policy_scope(enforce_language=False, on_preview=previews.append):
        api._request_analysis_response([UserMessage(SFTL_FIXTURE["request"])], stream=True)
    contents = [p.text for p in previews if p.kind == "content"]
    assert contents == [SFTL_FIXTURE["first_response"]] and raw_patch not in contents


def test_method_only_failed_device_candidate_retains_exact_text_in_receipt():
    from application.analysis_repair import plan_analysis_repair, apply_analysis_repair
    text = "禁止手工逐位移位替代 SFTL。"
    draft = json.loads(CURRENT_EMPTY)
    draft["explicit_constraint_claims"] = [{"operation": "forbid", "scope": "global",
        "target": {"kind": "device", "values": ["手工逐位移位"]}, "evidence": [text]}]
    plan = plan_analysis_repair(draft, text)
    repaired, receipt = apply_analysis_repair(plan, {"repairs": [{"path": "/explicit_constraint_claims/0", "value": []}]}, text)
    assert repaired["explicit_constraint_claims"] == []
    assert receipt["retained_text"][0]["evidence"] == [text]
    assert receipt["retained_text"][0]["unresolved_values"] == ["手工逐位移位"]
    assert receipt["retained_text"][0]["projection_status"] == "not_projected"


def test_local_representation_conversion_needs_no_model_call_and_no_evidence_fill():
    text = "固定 Y000。"
    draft = json.loads(CURRENT_EMPTY)
    draft["explicit_constraint_claims"] = [{"operation": "require", "scope": "global",
        "target": {"category": "devices", "values": ["Y0"]}, "evidence": [text]}]
    provider = Provider(json.dumps(draft, ensure_ascii=False))
    with api.provider_scope(provider):
        response = api._request_analysis_response([UserMessage(text)])
    assert len(provider.requests) == 1
    assert json.loads(response.message.content)["explicit_constraint_claims"] == [{
        "operation": "require", "scope": "global", "target": {"kind": "device", "values": ["Y0"]}, "evidence": [text]}]
    assert response.repair_receipt["model_paths"] == [] and response.repair_receipt["local"]


@pytest.mark.parametrize("stream", [False, True])
def test_sftl_first_analysis_with_grounded_targets_finishes_in_one_call(monkeypatch, stream):
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *args, **kwargs: "")
    instances = [
        ("M10", "M100"), ("M11", "M300"), ("M12", "M500"),
    ]
    first = {"summary": "三路物料位置跟踪", "approaches": [{"approach_id": "direct", "name": "同步移位跟踪",
        "decision_basis": [],
        "description": "运行保持及编码器同步移位。逐件登记方式等待工艺事实确认。", "pros": "", "cons": "", "generation_guide": "",
        "implementation_semantics": [{"kind": "structure", "status": "required", "value": name} for name in ["self_hold", "edge_trigger"]]}],
        "explicit_constraint_claims": [{"operation": "require", "scope": "global", "target": {
            "kind": "instruction_instance", "opcode": "SFTL", "operands": [source, base, "K128", "K1"]},
            "evidence": [f"SFTL {source} {base} K128 K1"]} for source, base in instances],
        "execution_intent_claims": [], "suggested_io": {}, "hardware_config": {}, "assumptions": [],
        "missing_info": [{"id": str(i), "question": question, "required": True, "options": ["待提供现场事实"]} for i, question in enumerate([
            "启动、停止按钮按下时输入分别为 0 还是 1？", "D0 是否保证在相邻物料之间回到 0？",
            "同一物料的分类结果能否改变？", "什么事件重新允许登记？", "登记结果与下一次 Encoder shift 怎样保持、消耗和清零？",
        ])]}
    provider = Provider(json.dumps(first, ensure_ascii=False))
    repairs = []
    function = api.analyze_requirement_streaming if stream else api.analyze_requirement
    with api.provider_scope(provider):
        result = function(SFTL_FIXTURE["request"], on_format_repair=lambda: repairs.append(True))
    assert len(provider.requests) == 1 and repairs == []
    assert result["missing_info"] == first["missing_info"]
    assert result["approaches"][0]["generation_contract"]["required_structures"] == ["self_hold", "edge_trigger"]
    assert len(result["approaches"][0]["generation_contract"]["instruction_instances"]) == 3


@pytest.mark.parametrize("damage", ["source", "from", "effect_devices", "rearm"])
def test_execution_repair_freezes_valid_metadata_but_can_fix_failed_fields(damage):
    from application.analysis_repair import plan_analysis_repair, apply_analysis_repair
    from application.analysis_results import validate_current_analysis_protocol, analysis_grounding_details
    text = "X1 从 0 变为 1 时让 M1 打一拍，松开后允许再次触发。"
    expected = {"trigger": {"kind": "transition", "source_devices": ["X1"], "from": 0, "to": 1},
                "effect": {"kind": "pulse", "devices": ["M1"]}, "rearm": "required", "evidence": [text]}
    broken = copy.deepcopy(expected)
    if damage == "source":
        broken["trigger"]["source_devices"] = "X1"
    elif damage == "from":
        broken["trigger"]["from"] = "unknown"
    elif damage == "effect_devices":
        broken["effect"]["devices"] = ["不是设备"]
    else:
        broken["rearm"] = {"bad": True}
    draft = json.loads(CURRENT_EMPTY)
    draft["execution_intent_claims"] = [broken]
    plan = plan_analysis_repair(draft, text)
    repaired, _ = apply_analysis_repair(plan, {"repairs": [{"path": "/execution_intent_claims/0", "value": [expected]}]}, text)
    assert repaired["execution_intent_claims"] == [expected]
    validate_current_analysis_protocol(repaired)
    assert analysis_grounding_details(repaired, text) == []


def test_failed_transition_receives_exact_legal_fields_in_short_repair_context():
    from application.analysis_repair import plan_analysis_repair, repair_messages
    text = "X004 每出现一次上升沿，表示输送带前进一个物料节拍。"
    draft = json.loads(CURRENT_EMPTY)
    draft["execution_intent_claims"] = [{"trigger": {"kind": "transition", "source_devices": ["X004"]}, "evidence": [text]}]
    plan = plan_analysis_repair(draft, text)
    request = json.loads(repair_messages(plan, text)[1]["content"])
    assert [t["path"] for t in request["targets"]] == ["/execution_intent_claims/0"]
    assert request["targets"][0]["errors"][0]["example"]["trigger"] == {
        "kind": "transition", "source_devices": ["X0"], "from": 0, "to": 1}
    assert request["targets"][0]["must_preserve"]["source_devices"] == ["X4"]


@pytest.mark.parametrize("replacement,valid", [(None, True), ({"kind": "structure", "status": "required", "value": "data_register_counter"}, False)])
def test_unsupported_structure_repair_does_not_select_approximate_label(replacement, valid):
    from application.analysis_repair import plan_analysis_repair, apply_analysis_repair
    from application.analysis_results import AnalysisProtocolError
    draft = json.loads(CURRENT_EMPTY)
    draft["approaches"][0]["implementation_semantics"] = [{"kind": "structure", "status": "required", "value": "shift_tracking"}]
    text = "使用 SFTL 跟踪。"
    plan = plan_analysis_repair(draft, text)
    patch = {"repairs": [{"path": "/approaches/0/implementation_semantics/0", "value": replacement}]}
    if valid:
        repaired, _ = apply_analysis_repair(plan, patch, text)
        assert repaired["approaches"][0]["implementation_semantics"] == []
    else:
        with pytest.raises(AnalysisProtocolError, match="not approximated"):
            apply_analysis_repair(plan, patch, text)
