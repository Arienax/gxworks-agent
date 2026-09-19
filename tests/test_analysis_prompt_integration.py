"""Repository integration tests. Providers are mocked; no paid requests."""
import json
from types import SimpleNamespace

import pytest

import application.model_api as api
import application.analysis_results as analysis_results
import application.generation_context as generation_context
import knowledge.retriever as retriever
from knowledge.evidence import context_manifest
from shared.context_policy import context_policy_scope


@pytest.mark.parametrize("streaming", [False, True])
def test_analysis_entry_uses_routed_assembly_and_one_model_call(monkeypatch, streaming):
    knowledge_calls, model_calls = [], []
    monkeypatch.setattr(api, "_resolve_plc_model", lambda *args: "FX3U")
    monkeypatch.setattr(api, "_load_plc_models", lambda: {"FX3U": {
        "addressing": "octal", "special_m": {"clock_01s": "M8012"},
        "soft_limits": {"X": "X000-X367", "Y": "Y000-Y367", "M": "M0-M7679"},
    }})
    monkeypatch.setattr(api, "_build_clean_messages", lambda history, prompt: [{"role": "system", "content": prompt}])
    monkeypatch.setattr(api, "_parse_analysis_response", lambda raw, *args: json.loads(raw))
    monkeypatch.setattr(analysis_results, "attach_analysis_evidence", lambda result, *args, **kwargs: result)

    def knowledge(query, **kwargs):
        knowledge_calls.append((query, kwargs))
        return "TARGETED_FACTS"
    def response(messages, **kwargs):
        model_calls.append((messages, kwargs))
        return SimpleNamespace(message=SimpleNamespace(content='{"summary":"ok"}'))
    monkeypatch.setattr(api, "_build_knowledge_context", knowledge)
    monkeypatch.setattr(api, "_request_analysis_response", response)
    function = api.analyze_requirement_streaming if streaming else api.analyze_requirement
    result = function("FX3U 使用 SFTL M10 M100 K128 K1，由 LDP M8012 触发")
    assert result["summary"] == "ok"
    assert len(model_calls) == 1
    assert model_calls[0][1]["stream"] is streaming
    assert len(knowledge_calls) == 1
    assert knowledge_calls[0][1]["include_design"] is False
    prompt = model_calls[0][0][0]["content"]
    assert "Analysis mode: pinned / extract" in prompt
    assert "TARGETED_FACTS" in prompt
    assert "PLC workflow router" not in prompt
    assert "Scan cycle and output ownership review" not in prompt
    assert "Relevant questions: motion" not in prompt


def _fact(identifier="manual:SFTL", text="SFTL fact"):
    return {"id": identifier, "manual_id": "fx3_programming", "chunk_type": "instruction",
            "title": "SFTL", "text": text, "page": 1}


def _design():
    return {"id": "design:state", "manual_id": "curated_control_design", "chunk_type": "design_pattern",
            "title": "Architecture", "text": "DESIGN_PATTERN_SENTINEL", "page": 1}


def test_pinned_retrieval_never_calls_design_lane_or_leaks_design_as_fact(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Pinned requests must not call design RAG")
    monkeypatch.setattr(retriever, "retrieve_design_knowledge", unexpected)
    monkeypatch.setattr(retriever, "retrieve_knowledge", lambda *args, **kwargs: [_design(), _fact()])
    monkeypatch.setattr(retriever._core, "_format_result_block", lambda item: item["text"])
    context = retriever.build_knowledge_context(
        "FX3U SFTL M8012", task_type="analysis", include_design=False, top_k=4, char_budget=12000)
    assert "SFTL fact" in context
    assert "DESIGN_PATTERN_SENTINEL" not in context
    assert context_manifest(context)["design_enabled"] is False


def test_open_retrieval_uses_separate_query_and_preserves_fact_lane(monkeypatch):
    seen = []
    def design(query, **kwargs):
        seen.append(query)
        return [_design()]
    monkeypatch.setattr(retriever, "retrieve_design_knowledge", design)
    monkeypatch.setattr(retriever, "retrieve_knowledge", lambda *args, **kwargs: [_fact()])
    monkeypatch.setattr(retriever._core, "_format_result_block", lambda item: item["text"])
    context = retriever.build_knowledge_context(
        "FX3U SFTL", task_type="analysis", include_design=True, design_query="compare sorting architectures",
        top_k=4, char_budget=12000)
    assert seen == ["compare sorting architectures"]
    assert "SFTL fact" in context and "DESIGN_PATTERN_SENTINEL" in context
    assert context_manifest(context)["design_enabled"] is True


def test_generation_does_not_gain_design_retrieval(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Generation must not retrieve designs")
    monkeypatch.setattr(retriever, "retrieve_design_knowledge", unexpected)
    monkeypatch.setattr(retriever, "retrieve_knowledge", lambda *args, **kwargs: [_fact()])
    monkeypatch.setattr(retriever._core, "_format_result_block", lambda item: item["text"])
    assert "SFTL fact" in retriever.build_knowledge_context("SFTL", task_type="generate", include_design=True)


def test_application_passes_analysis_policy_to_retriever(monkeypatch):
    seen = []
    def capture(query, **kwargs):
        seen.append(kwargs)
        return "FACT_EVIDENCE"
    monkeypatch.setattr(retriever, "build_knowledge_context", capture)
    with context_policy_scope("adaptive"):
        context = generation_context._build_knowledge_context(
            "FX3U SFTL M8012", task_type="analysis", include_design=False)
    assert "FACT_EVIDENCE" in context
    assert seen[0]["include_design"] is False
    assert seen[0]["design_query"] is None


def test_direct_analysis_knowledge_call_is_also_routed(monkeypatch):
    seen = []
    def capture(query, **kwargs):
        seen.append(kwargs)
        return "FACT_EVIDENCE"
    monkeypatch.setattr(retriever, "build_knowledge_context", capture)
    with context_policy_scope("adaptive"):
        generation_context._build_knowledge_context(
            "FX3U 使用 SFTL M10 M100 K128 K1", task_type="analysis")
    assert seen[0]["include_design"] is False
