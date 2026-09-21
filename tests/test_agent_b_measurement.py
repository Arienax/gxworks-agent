"""Actual generation-path measurement contract; all providers here are offline."""
import copy
import json
from types import SimpleNamespace

import pytest

from application.compact_protocol import canonical_compact_example, compact_response_schema, compact_protocol_prompt
from application.generation_agent import _FirstJSONObjectProvider
from model_runtime.provider import ModelRequest, OpenAICompatibleProvider, ReasoningDelta, TextDelta, Usage, UserMessage, _usage_event
from scripts.benchmark_agent_b import ObservedProvider, main, run_case, schedule, summarize


@pytest.mark.parametrize("details", ["completion_tokens_details", "output_tokens_details"])
def test_reported_reasoning_usage_is_preserved_including_empty_choice_tail(details, monkeypatch):
    captured = []
    import shared.diagnostics as diagnostics
    monkeypatch.setattr(diagnostics, "emit", lambda event, **fields: captured.append((event, fields)))
    provider = OpenAICompatibleProvider({"adapter": "openai_compatible", "model": "offline"}, "offline-key", client=object())
    metering = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, details: {"reasoning_tokens": 15}}
    response = [
        {"choices": [{"delta": {"content": '{"r":[]}'}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": metering},
    ]
    events = list(provider._streaming_events(response))
    usage = next(event for event in events if isinstance(event, Usage))
    assert usage.reasoning_tokens == 15 and usage.raw_usage == metering
    assert captured[-1][1]["reasoning_tokens"] == 15 and captured[-1][1]["finish_seen"]


def test_missing_reasoning_meter_is_unknown_not_zero():
    usage = _usage_event({"prompt_tokens": 10, "completion_tokens": 20})
    assert usage.reasoning_tokens is None
    assert "reasoning_tokens" not in usage.raw_usage
    assert _usage_event({"input_tokens": 10, "output_tokens": 20, "output_tokens_details": {"reasoning_tokens": 0}}).reasoning_tokens == 0


def test_json_framing_does_not_drop_reasoning_or_usage_and_observation_sees_duplicates():
    class Provider:
        profile = {}
        def stream(self, request):
            yield ReasoningDelta("先核对事实")
            yield TextDelta('{"r":[]}')
            yield TextDelta('{"r":["duplicate"]}')
            yield ReasoningDelta("保留提供商实际返回的后续记录")
            yield Usage(1, 2, 3, 1)
    observed = ObservedProvider(Provider())
    request = ModelRequest((UserMessage("fixture"),), options={"reasoning_effort": "high"}, stream=True)
    events = list(_FirstJSONObjectProvider(observed).stream(request))
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == '{"r":[]}'
    assert len([event for event in events if isinstance(event, ReasoningDelta)]) == 2
    assert any(isinstance(event, Usage) and event.reasoning_tokens == 1 for event in events)
    attempt = observed.attempts[0]
    assert 'duplicate' in attempt["raw_content"] and attempt["transport_completed"]
    assert attempt["first_content_ms"] <= attempt["json_complete_ms"] <= attempt["transport_ms"]
    assert request.options == {"reasoning_effort": "high"}


def test_prompt_and_schema_have_one_canonical_required_shape():
    import jsonschema
    example = canonical_compact_example()
    jsonschema.validate(example, compact_response_schema())
    schema = compact_response_schema()
    prompt = compact_protocol_prompt()
    rung = schema["properties"]["r"]["items"]
    branch = rung["properties"]["b"]["items"]
    assert "顶层对象必填字段：" + "、".join(schema["required"]) in prompt
    assert "每个梯级必填字段：" + "、".join(rung["required"]) in prompt
    assert "每个输出分支必填字段：" + "、".join(branch["required"]) in prompt
    assert '"root"' not in prompt
    assert "这些字段不省略" in compact_protocol_prompt()


@pytest.mark.parametrize("root_key", ["r", "root"])
def test_runner_uses_real_single_call_path_without_changing_effort(monkeypatch, root_key):
    from test_confirmed_input_protocol import old_confirmed_spec, compact
    from knowledge.evidence import KnowledgeContext
    import application.generation_agent as agent
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: KnowledgeContext("", {"records": []}))
    class Provider:
        profile = {"id": "offline", "adapter": "openai_compatible", "model": "offline", "capabilities": {"structured_output": True}}
        def __init__(self):
            self.requests = []
        def stream(self, request):
            self.requests.append(request)
            yield TextDelta(json.dumps({root_key: compact()["r"]}))
            yield Usage(10, 20, 30, 12)
    provider = Provider()
    record = run_case({"case_id": "hold", "confirmed_spec": old_confirmed_spec()}, "automatic", provider=provider, effort="high")
    assert record["generation_status"] == "completed", record
    assert record["behavior"]["status"] == "verified" and record["structural_valid"]
    assert record["model_calls"] == 1 and provider.requests[0].max_retries == 0
    assert provider.requests[0].options["reasoning_effort"] == "high"
    assert record["attempts"][0]["usage"]["reasoning_tokens"] == 12
    assert agent._build_knowledge_context("fixture").manifest == {"records": []}


def test_dry_run_never_loads_provider_or_calls_model(tmp_path, monkeypatch, capsys):
    import model_runtime.provider as provider
    monkeypatch.setattr(provider, "get_active_provider", lambda: pytest.fail("Dry-run must not access credentials"))
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps({"case_id": "fixture", "confirmed_spec": {"summary": "example"}}), encoding="utf-8")
    assert main([str(path), "--repeat", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["scheduled_runs"] == 4


def test_failure_and_unknown_usage_are_not_silently_dropped():
    rows = [{"arm": "automatic", "end_to_end_ms": 100, "generation_status": "failed",
             "behavior": {"status": "not_covered"}, "attempts": [{"usage": None}]},
            {"arm": "automatic", "end_to_end_ms": 200, "generation_status": "completed",
             "behavior": {"status": "verified"}, "attempts": [{"usage": {"reasoning_tokens": 20}}]}]
    result = summarize(rows)
    assert result["groups"]["automatic"]["runs"] == 2
    assert result["groups"]["automatic"]["reasoning_usage_known_runs"] == 1
    assert result["performance_acceptance"] == "not_established"
    tasks = schedule([{"case_id": "a"}, {"case_id": "b"}], ["automatic", "legacy_retrieval"], 3, 7)
    assert len(tasks) == 12 and tasks == schedule([{"case_id": "a"}, {"case_id": "b"}], ["automatic", "legacy_retrieval"], 3, 7)


def test_transcript_metering_and_custom_headers_remain_credential_redacted(tmp_path):
    from shared.tracing import sanitize, record_model_response
    headers = {"extra_headers": {"X-Api-Key": "nonstandard-secret", "Cookie": "session-secret"}}
    assert sanitize(headers) == {"extra_headers": {"X-Api-Key": "<redacted>", "Cookie": "<redacted>"}}
    request = ModelRequest((UserMessage("fixture"),))
    from model_runtime.provider import AssistantMessage, RawModelResponse
    raw = RawModelResponse(AssistantMessage("{}"), Usage(10, 20, 30, 12, {"completion_tokens_details": {"reasoning_tokens": 12}}), (), False, "")
    assert record_model_response(tmp_path, "job_fixture", 1, 1, raw, request)
    row = json.loads((tmp_path / "diagnostics/job_fixture.transcript.jsonl").read_text())
    assert row["usage"]["reasoning_tokens"] == 12
    assert row["usage"]["raw_usage"]["completion_tokens_details"]["reasoning_tokens"] == 12
