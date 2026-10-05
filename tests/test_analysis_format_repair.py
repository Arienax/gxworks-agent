"""A malformed real GLM flowchart must not strand an analysis draft."""
import copy
import json

import pytest

import application.model_api as api
from model_runtime.provider import (
    ModelProviderError, ReasoningDelta, ResponseRejectedError,
    TextDelta, UserMessage,
)
from application.response_contracts import ANALYSIS_RESPONSE


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
    assert second.messages[:-2] == first.messages
    assert second.messages[-2].content == BROKEN
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
    provider = Provider(first, CURRENT_EMPTY)
    events = []
    with api.provider_scope(provider):
        result = api._request_analysis_response(
            [UserMessage("起保停")],
            on_format_repair=lambda: events.append("repair"),
        )
    assert result.message.content == CURRENT_EMPTY
    assert len(provider.requests) == 2
    assert events == ["repair"]
    correction = provider.requests[1].messages[-1].content
    assert "implementation_semantics" in correction
    assert "generation_contract" in correction
    assert provider.requests[1].messages[-2].content == first


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
                "evidence": ["对 C0 计数一次；"],
            },
        ],
        "missing_info": [],
        "suggested_io": {},
        "hardware_config": {},
        "assumptions": [],
    }, ensure_ascii=False)
    provider = Provider(content)
    with api.provider_scope(provider):
        result = api._request_analysis_response([UserMessage("复杂事件")])
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

    provider = Provider(LEGACY_PROTOCOL, MIXED_PROTOCOL)
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
