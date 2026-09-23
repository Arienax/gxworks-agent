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


# Minimized from the 2026-09-10 GLM stream, which ended with finish_reason=stop
# even though the transition object was missing its label key.
BROKEN = '{"summary":"起保停控制","approaches":[],"flowchart_steps":[{"type":"transition":"X0启动"}]}'
FIXED = '{"summary":"起保停控制","approaches":[],"flowchart_steps":[{"type":"transition","label":"X0启动"}]}'
LEGACY_PROTOCOL = '{"summary":"起保停控制","approaches":[{"name":"旧协议","generation_contract":{"required_structures":["self_hold"]}}]}'
CURRENT_EMPTY = '{"summary":"起保停控制","approaches":[{"name":"当前协议","implementation_semantics":[]}]}'
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
        "summary", "approaches", "missing_info", "suggested_io", "hardware_config", "assumptions",
    }
