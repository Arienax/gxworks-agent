"""Offline regressions for shared semantic contracts and bounded model calls."""
from __future__ import annotations

import ast
import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import application.generation_agent as agent_b
import application.model_api as api
import knowledge.retriever as retrieval
from application.confirmed_generation_context import (
    CONFIRMED_GENERATION_REQUEST, build_confirmed_generation_context,
    project_confirmed_specification,
)
from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
from plc.candidate_repair import GenerationError
from plc.instructions import generation_app_instr_mnemonics
from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
from model_runtime.provider import (
    ModelProviderError, ModelRequest, ReasoningDelta, TextDelta, ToolCall,
    ToolCallEnd, ToolCallStart, Usage, UserMessage, collect_response,
)
from shared.context_policy import context_policy_scope
from shared.i18n import language_context


def ladder():
    return {"device_comments": {"X0": "Start", "Y0": "Run"}, "rungs": [{
        "rung_id": 1, "header_element": None, "shared_inputs": [], "branches": [{
            "branch_id": 1, "y_offset_level": 0,
            "inputs": [{"type": "NO", "address": "X0"}],
            "outputs": [{"type": "COIL", "address": "Y0"}],
        }],
    }]}


def specification(model="FX3U"):
    return {"plc_model": model, "summary": "Confirmed SFTL shift control",
            "io_table": [{"kind": "X", "address": "X000", "label": "Start"},
                         {"kind": "Y", "address": "Y000", "label": "Run"}],
            "io_bindings": [{"binding_id": "start", "kind": "X", "address": "X000",
                             "role": "start", "name": "Start", "private": "PRIVATE_BINDING"}],
            "selected_approach": {"name": "SELECTED_SHIFT_PLAN", "description": "SELECTED_SHIFT_DESCRIPTION",
                                  "generation_guide": "SELECTED_SHIFT_GUIDE",
                                  "generation_contract": {"required_opcodes": ["SFTL"],
                                                          "forbidden_opcodes": ["SFTR"]}},
            "history": [{"content": "PRIVATE_HISTORY"}], "api_key": "PRIVATE_CREDENTIAL"}


def opcode_enums(schema):
    found = []
    def visit(value):
        if isinstance(value, dict):
            properties = value.get("properties", {})
            if "APP_INSTR" in properties.get("type", {}).get("enum", []):
                found.append(properties["opcode"].get("enum"))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(schema)
    return found


@pytest.mark.parametrize("model", ["FX3U", "FX5U"])
@pytest.mark.parametrize("editing", [False, True])
def test_mcp_full_and_partial_opcode_enums_exactly_match_selected_registry(monkeypatch, model, editing):
    monkeypatch.setattr(retrieval, "build_knowledge_context", lambda *a, **k: "")
    context = build_tool_context({"id": "schema", "plc_model": model},
                                 ladder=ladder() if editing else None)
    result = build_default_tool_registry().call("get_generation_context", {}, context)
    assert result["ok"], result
    schema = result["data"]["output_contract"]["schema"]
    enums = opcode_enums(schema)
    assert enums and all(values == list(generation_app_instr_mnemonics(model)) for values in enums)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(ladder())


@pytest.mark.parametrize("model", ["FX3U", "FX5U"])
def test_compact_and_mcp_adapters_share_confirmed_facts_and_retrieval(monkeypatch, model):
    calls = []
    def retrieve(query, **kwargs):
        calls.append((query, copy.deepcopy(kwargs)))
        return "Shared official SFTL evidence."
    monkeypatch.setattr(retrieval, "build_knowledge_context", retrieve)
    spec = specification(model)
    original = copy.deepcopy(spec)
    with context_policy_scope("legacy"):
        compact_prompt = agent_b._build_agent_b_prompt(spec, model)
        compact_calls = copy.deepcopy(calls)
        calls.clear()
        result = build_default_tool_registry().call("get_generation_context", {
            "user_requirement": "PRIVATE_UNCONFIRMED_REQUEST: add a VFD and X9",
        }, build_tool_context({"id": "semantics", "plc_model": model, "confirmed_spec": spec}))
    assert result["ok"], result
    data = result["data"]
    assert calls == compact_calls and len(calls) == 1
    compact_spec = json.JSONDecoder().raw_decode(compact_prompt.split("# Confirmed project specification\n", 1)[1])[0]
    full_spec = json.JSONDecoder().raw_decode(data["generation_instructions"].split("# Confirmed project specification\n", 1)[1])[0]
    assert compact_spec == full_spec == data["confirmed_spec"] == project_confirmed_specification(spec)
    assert data["generation_request"] == CONFIRMED_GENERATION_REQUEST
    assert compact_spec["io_bindings"][0]["address"] == "X0"
    assert compact_spec["selected_approach"]["generation_contract"]["required_opcodes"] == ["SFTL"]
    wire = compact_prompt + json.dumps(data)
    for key in ("name", "description", "generation_guide"):
        assert compact_spec["selected_approach"][key] == spec["selected_approach"][key]
    for private in ("PRIVATE_HISTORY", "PRIVATE_CREDENTIAL", "PRIVATE_BINDING", "PRIVATE_UNCONFIRMED_REQUEST"):
        assert private not in wire
    assert spec == original
    assert "Agent B compact" in compact_prompt and "Machine-readable output schema" in data["generation_instructions"]


def test_shared_snapshot_is_detached_and_edit_delta_and_baseline_are_retained():
    spec, current, captured = specification(), ladder(), []
    original_spec, original_current = copy.deepcopy(spec), copy.deepcopy(current)
    def knowledge(query, **kwargs):
        captured.append((query, kwargs))
        return "Manual evidence from C:/Users/private/manual.txt; api_key=secret-key"
    context = build_confirmed_generation_context(spec, "fx3u", user_requirement="Replace X0 with X1",
        current_program=current, evidence=current, task_type="edit", knowledge_builder=knowledge)
    assert context.generation_request == "Replace X0 with X1"
    assert context.current_program == current
    assert "private/manual.txt" not in context.knowledge_context and "secret-key" not in context.knowledge_context
    spec["io_table"].clear()
    current["rungs"].clear()
    assert context.current_program == original_current
    assert context.confirmed_spec == project_confirmed_specification(original_spec)
    exported = context.to_dict()
    exported["confirmed_spec"].clear()
    assert context.confirmed_spec
    assert captured[0][1]["task_type"] == "edit"


class EventsProvider:
    def __init__(self, rounds, profile=None):
        self.rounds = list(rounds)
        self.requests = []
        self.profile = profile or {}
        self.api_key = "key"

    def stream(self, request):
        self.requests.append(request)
        for item in self.rounds.pop(0):
            if isinstance(item, Exception):
                raise item
            yield item


def request():
    return ModelRequest((UserMessage("Return the result."),), response_language="en", stream=True)


ERRORS = [("authentication", 401), ("authentication", 403), ("invalid_request", 400),
          ("rate_limit", 429), ("unavailable", 500), ("unavailable", 503),
          ("unavailable", None), ("timeout", None), ("protocol", None),
          ("provider_error", None), ("stream_protocol_error", None)]


@pytest.mark.parametrize("code,status", ERRORS)
def test_runtime_never_replays_unrelated_transport_failures(code, status):
    provider = EventsProvider([[ModelProviderError("PRIVATE_ERROR", code=code, status_code=status)]])
    fallbacks = []
    with pytest.raises(ModelProviderError) as failure:
        collect_response(provider, request(), fallback_to_non_stream=True, on_fallback=fallbacks.append)
    assert len(provider.requests) == 1 and fallbacks == []
    assert "PRIVATE_ERROR" not in str(failure.value)


@pytest.mark.parametrize("event", [TextDelta("partial"), ReasoningDelta("reasoning"),
    ToolCallStart("1", "tool"), ToolCallEnd(ToolCall("1", "tool", {})), Usage(1, 1, 2)])
def test_even_explicit_stream_rejection_cannot_replay_after_any_event(event):
    provider = EventsProvider([[event, ModelProviderError("No replay", code="stream_not_supported")]])
    published = []
    with pytest.raises(ModelProviderError) as failure:
        collect_response(provider, request(), fallback_to_non_stream=True,
                         on_content_chunk=published.append, on_event=published.append)
    assert len(provider.requests) == 1 and published == []
    assert failure.value.raw_attempts[0].events == (event,)


def test_explicit_stream_rejection_replays_once_with_identical_frozen_request():
    provider = EventsProvider([[ModelProviderError("No stream", code="stream_not_supported", status_code=400)],
                               [TextDelta("Accepted result.")]])
    original = replace(request(), options={"temperature": .17}, max_retries=0)
    callbacks = []
    result = collect_response(provider, original, fallback_to_non_stream=True, on_content_chunk=callbacks.append)
    assert result.message.content == "Accepted result." and callbacks == ["Accepted result."]
    first, second = provider.requests
    assert second == replace(first, stream=False)
    assert len(result.raw_attempts) == 2 and original.stream is True


@pytest.mark.parametrize("body,allowed", [
    ({"error": {"code": "unsupported_parameter", "param": "stream"}}, True),
    ({"error": {"code": "stream_not_supported"}}, True),
    ({"error": {"code": "unsupported_parameter", "param": "temperature"}}, False),
    ({"error": {"message": "stream is not supported"}}, False),
    ({"error": {"code": "invalid_value", "param": "stream"}}, False),
])
def test_only_structured_stream_rejection_authorizes_fallback(body, allowed):
    error = RuntimeError("PRIVATE_ERROR")
    error.status_code, error.body = 400, body
    provider = EventsProvider([[error], [TextDelta("Accepted result.")]])
    if allowed:
        assert collect_response(provider, request(), fallback_to_non_stream=True).message.content == "Accepted result."
        assert len(provider.requests) == 2
    else:
        with pytest.raises(ModelProviderError):
            collect_response(provider, request(), fallback_to_non_stream=True)
        assert len(provider.requests) == 1


@pytest.mark.parametrize("code,status", ERRORS)
@pytest.mark.parametrize("editing", [False, True])
def test_generation_and_edit_workflows_never_retry_unrelated_errors(tmp_path, monkeypatch, code, status, editing):
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **k: "")
    monkeypatch.setattr(api, "generate_model_json", lambda *a, **k: pytest.fail("Application replayed generation"))
    provider = EventsProvider([[ModelProviderError("PRIVATE_ERROR", code=code, status_code=status)]])
    workflow = GenerationWorkflow(GenerationRequest("Generate", plc_model="FX3U", model_name="offline",
        previous_json=ladder() if editing else None), tmp_path,
        dependencies=GenerationDependencies(provider=provider))
    with pytest.raises(GenerationError):
        workflow.run()
    assert len(provider.requests) == 1 and list(tmp_path.iterdir()) == []
    assert provider.requests[0].max_retries == 0


def test_no_second_generation_after_an_empty_injected_result(tmp_path):
    calls = []
    def stream(*a, **k):
        calls.append("stream")
        return "", ""
    def nonstream(*a, **k):
        pytest.fail("Empty output triggered a paid replay")
    with pytest.raises(GenerationError):
        GenerationWorkflow(GenerationRequest("Generate", model_name="offline"), tmp_path,
            dependencies=GenerationDependencies(stream_response=stream, generate_json=nonstream)).run()
    assert calls == ["stream"]


def test_generation_runtime_fallback_is_bounded_and_keeps_history(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **k: "")
    monkeypatch.setattr(api, "generate_model_json", lambda *a, **k: pytest.fail("Application replay"))
    provider = EventsProvider([[ModelProviderError("No stream", code="stream_not_supported")],
        [TextDelta(json.dumps({"st_code": "Y0 := X0;"}))]])
    result = GenerationWorkflow(GenerationRequest("Generate", target_mode="st", model_name="offline"), tmp_path,
        dependencies=GenerationDependencies(provider=provider)).run()
    assert result["artifacts"] == {"st": "program.st"}
    assert [r.stream for r in provider.requests] == [True, False]
    assert provider.requests[1] == replace(provider.requests[0], stream=False)


def test_known_nonstream_profile_never_sends_a_stream_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **k: "")
    from test_model_contract import make_profile
    profile = make_profile(capabilities={"streaming": {"status": "unsupported"}})
    provider = EventsProvider([[TextDelta('{"st_code":"Y0 := X0;"}')]], profile)
    GenerationWorkflow(GenerationRequest("Generate", target_mode="st", model_name=profile["model"]), tmp_path,
        dependencies=GenerationDependencies(provider=provider)).run()
    assert [r.stream for r in provider.requests] == [False]


@pytest.mark.parametrize("selection,expected", [("inherit", None), ("balanced", "balanced"), ("omit", None)])
def test_tool_agent_does_not_assume_provider_effort_vocabulary(selection, expected):
    from test_model_contract import make_profile, select
    from model_runtime.request_policy import resolve_request
    from agent_runtime.agent import run_tool_agent
    p = make_profile({"reasoning_effort": {"type": "enum", "values": ["economy", "balanced", "thorough"]}})
    if selection != "inherit":
        select(p, reasoning_effort=None if selection == "omit" else selection)
    before = copy.deepcopy(p)
    class Provider(EventsProvider):
        def stream(self, request):
            self.requests.append(request)
            self.effective = resolve_request(self.profile, request.options, model=request.model, api_key="key")
            yield TextDelta("Completed.")
    provider = Provider([], p)
    with language_context("en"):
        run_tool_agent("Inspect", context=build_tool_context({"id": "effort", "plc_model": "FX3U"}), provider=provider)
    assert len(provider.requests) == 1
    assert "reasoning_effort" not in provider.requests[0].options
    assert provider.effective.options.get("reasoning_effort") == expected
    assert p == before


def test_candidate_service_is_entered_by_web_and_real_mcp_paths(tmp_path, monkeypatch):
    from plc.candidate_service import CandidateService
    from test_generation_path_parity import _parity, _project, _ladder
    visits = []
    prepare, compile_ = CandidateService.prepare, CandidateService.compile
    def preparing(self, *a, **k):
        visits.append("prepare")
        return prepare(self, *a, **k)
    def compiling(self, *a, **k):
        visits.append("compile")
        return compile_(self, *a, **k)
    monkeypatch.setattr(CandidateService, "prepare", preparing)
    monkeypatch.setattr(CandidateService, "compile", compiling)
    store, project_id = _project(tmp_path)
    _parity(_ladder(), store, project_id, tmp_path / "api", monkeypatch)
    assert visits == ["prepare", "compile", "prepare", "compile"]


def test_agent_b_uses_public_gateway_and_remains_one_completion(monkeypatch):
    spec = specification()
    monkeypatch.setattr(agent_b, "_build_knowledge_context", lambda *a, **k: "")
    provider = EventsProvider([[ModelProviderError("No stream", code="stream_not_supported")]])
    monkeypatch.setattr(api, "current_provider", lambda: provider)
    calls = []
    owned_request = api.request_model
    def gateway(*a, **k):
        calls.append(k)
        return owned_request(*a, **k)
    monkeypatch.setattr(api, "request_model", gateway)
    with pytest.raises(ModelProviderError):
        agent_b.generate_confirmed_ladder(spec, model_name="offline")
    assert len(calls) == len(provider.requests) == 1
    assert calls[0]["max_retries"] == 0 and not calls[0].get("fallback_to_non_stream", False)
    tree = ast.parse(Path(agent_b.__file__).read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name) and n.value.id == "api" and n.attr.startswith("_")]


def test_opaque_provider_state_also_blocks_transport_replay():
    from model_runtime.provider import _ProviderState
    provider = EventsProvider([[_ProviderState({}), ModelProviderError("No stream", code="stream_not_supported")]])
    with pytest.raises(ModelProviderError):
        collect_response(provider, request(), fallback_to_non_stream=True)
    assert len(provider.requests) == 1


def test_explicit_bad_effort_is_not_silently_mapped_or_dropped():
    from test_model_contract import make_profile, select
    from model_runtime.request_policy import resolve_request
    from agent_runtime.agent import run_tool_agent
    p = make_profile({"reasoning_effort": {"type": "enum", "values": ["economy", "balanced", "thorough"]}})
    select(p, reasoning_effort="high")
    before, sent = copy.deepcopy(p), []
    class Provider(EventsProvider):
        def stream(self, request):
            resolve_request(self.profile, request.options, model=request.model, api_key="key")
            sent.append(request)
            yield TextDelta("Completed.")
    with pytest.raises(ModelProviderError):
        run_tool_agent("Inspect", context=build_tool_context({"id": "effort", "plc_model": "FX3U"}),
                       provider=Provider([], p))
    assert sent == [] and p == before


def test_parameterized_test_renames_do_not_hide_missing_cases():
    import runpy
    compare = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/source_layout_regression.py"))["compare_reports"]
    old = "tests.test_provider_error_privacy.test_generation_injected_transport_error_is_safe_and_still_falls_back[True]"
    new = "tests.test_provider_error_privacy.test_generation_injected_transport_error_is_safe_and_never_replayed[True]"
    result = compare({old: {"status": "passed"}}, {new: {"status": "passed"}})
    assert result["regression_gate_passed"] and result["reviewed_test_renames"] == {old: new}
    assert not compare({old: {"status": "passed"}}, {})["regression_gate_passed"]
    assert not compare({old: {"status": "passed"}}, {new: {"status": "failed", "message": "failure"}})["regression_gate_passed"]
