"""A malformed real GLM flowchart must not strand an analysis draft."""
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
CURRENT_EMPTY = '{"summary":"起保停控制","approaches":[{"name":"当前协议","implementation_semantics":[]}],"execution_intent_claims":[],"explicit_constraint_claims":[]}'
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


@pytest.mark.parametrize("stream", [False, True])
def test_real_analysis_path_corrects_syntax_once_before_publishing(stream, monkeypatch):
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **kw: "")
    provider = Provider(BROKEN, FIXED)
    events = []
    callback = lambda: events.append("repair")
    options = {"on_format_repair": callback, "response_language": "zh-CN"}
    if stream:
        options["on_content_chunk"] = lambda text: events.append(text)
    function = api.analyze_requirement_streaming if stream else api.analyze_requirement
    with api.provider_scope(provider, model_name="captured-profile"):
        result = function("起保停", **options)
    assert result["summary"] == "起保停控制"
    assert "flowchart_steps" not in result  # Old replies remain parseable, not new model work.
    assert len(provider.requests) == 2
    first, second = provider.requests
    assert first.stream is second.stream is stream
    assert first.response_contract == second.response_contract == ANALYSIS_RESPONSE
    assert first.response_language == second.response_language == "zh-CN"
    assert first.options == second.options
    assert len(second.messages) == 2
    assert json.loads(second.messages[-1].content)["rejected_draft"] == BROKEN
    assert "line 1, column" in second.messages[-1].content
    assert not first.tools and not second.tools
    assert events == (["repair", FIXED] if stream else ["repair"])


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
def test_bound_source_requires_its_own_evidence_before_publication(stream):
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
    request = json.loads(provider.requests[1].messages[-1].content)
    assert [target["path"] for target in request["targets"]] == ["/execution_intent_claims/0"]
    assert request["targets"][0]["errors"][0]["code"] == "claimed_device_not_in_evidence"
    assert request["targets"][0]["must_preserve"]["source_devices"] == ["M0"]
    assembled = json.loads(response.message.content)
    assert assembled["execution_intent_claims"] == replacement
    assert assembled["missing_info"] == draft["missing_info"]


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


SFTL_FIXTURE = json.loads((Path(__file__).parent / "fixtures/analysis_sftl_repair.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("stream", [False, True])
def test_real_sftl_draft_repairs_claims_and_freezes_every_other_field(stream):
    from application.analysis_results import _normalize_analysis_result
    from application.analysis_repair import plan_analysis_repair
    text = SFTL_FIXTURE["request"]
    raw = SFTL_FIXTURE["first_response"]
    patch = json.dumps(SFTL_FIXTURE["repair_response"], ensure_ascii=False)
    provider = Provider(raw, patch)
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
